"""Supervise persistent subprocesses for native schematic operations.

The native engine can abort its interpreter, and cancelling a Python await cannot stop native
work running in a thread. Child processes let the pool contain those failures and terminate an
operation that exceeds its deadline. Children stay alive between requests so rendering can reuse
parsed resource packs instead of loading them for every image.

Each worker handles one request at a time. Operation deadlines cover waiting for a pool slot,
restart backoff, and the request itself. Renders additionally wait for a shared render slot
before that deadline starts. If a worker request times out, the supervisor kills the worker
(its process group on POSIX) and briefly collects diagnostics.

Failed requests are not replayed, since the same input may crash its replacement too. A later
request can start a replacement worker, subject to restart backoff and a circuit breaker that
temporarily refuses work after repeated failures.
"""

import asyncio
import base64
import contextlib
import dataclasses
import functools
import json
import logging
import os
import signal
import sys
from collections import deque
from collections.abc import AsyncGenerator, Callable, Coroutine, Mapping, Sequence
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any, Self, cast

import anyio
from anyio.abc import TaskGroup

from squid.config import SchematicConfig
from squid.core.errors import DomainError, InfrastructureError
from squid.observability import add_counter, record_gauge, record_histogram, trace_context_headers
from squid.schematics.application.commands import RenderRequest, SimulationRequest
from squid.schematics.domain.models import (
    AnalyzerCapabilities,
    AutostackLattice,
    FingerprintPreset,
    SchematicAnalysis,
    SchematicComparison,
    SchematicFormat,
    SchematicLimits,
    SimulationResult,
    VersionLossEntry,
)
from squid.schematics.domain.values import VerifiedResourcePack
from squid.schematics.errors import (
    InvalidSchematicError,
    SchematicSupportUnavailableError,
    SchematicTimeoutError,
    SchematicTooLargeError,
    SchematicWorkerCrashedError,
)
from squid.schematics.infrastructure import wire
from squid.schematics.infrastructure.wire import Frame, FrameStreamClosed, Operation

logger = logging.getLogger(__name__)

worker_logger = logging.getLogger("squid.schematics.worker")
"""Logger for stderr that cannot be reconstructed as one of the child's structured records."""

WORKER_MODULE = "squid.schematics.infrastructure.worker_main"

current_schematic_job_id: ContextVar[int | None] = ContextVar("squid.schematic_job_id", default=None)
"""Optional durable job ID copied into request frames for child log correlation.

A context variable carries this queue-specific detail without adding a job ID to every operation
in the shared SchematicAnalyzer interface.
"""

STDERR_LINE_LIMIT = 1024 * 1024
"""Allow large structured tracebacks; StreamReader.readline drops lines over this limit."""

WORKER_FAILURE_CLEANUP_SECONDS = 0.1
"""Extra time to collect exit status and buffered logs after killing a timed-out or crashed worker."""


@dataclasses.dataclass(slots=True)
class _StderrPump:
    """The cancellation scope and completion signal for a worker's stderr reader.

    The pool owns the task. A worker uses these handles to drain buffered diagnostics and stop
    the reader during shutdown without holding the task object itself.
    """

    scope: anyio.CancelScope
    finished: anyio.Event

    async def drain(self, timeout: float) -> None:
        """Wait for the pipe to reach end of file, then stop the pump either way."""
        with anyio.move_on_after(timeout):
            await self.finished.wait()
        self.scope.cancel()
        await self.finished.wait()


type _PumpBody = Callable[[], Coroutine[Any, Any, None]]
type _SpawnPump = Callable[[_PumpBody], None]
"""How a worker hands a pump to whoever owns its lifetime — see `SchematicWorkerPool.running`.

A Coroutine rather than an Awaitable because that is what TaskGroup.start_soon accepts.
"""


class _Worker:
    """One child process and the single request slot that guards it."""

    def __init__(self, config: SchematicConfig, spawn: _SpawnPump) -> None:
        self._config = config
        self._spawn = spawn
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_pump: _StderrPump | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._started_once = False
        self._restart_not_before = 0.0

    async def request(
        self, operation: Operation, params: Mapping[str, Any], payloads: Sequence[bytes], timeout: float
    ) -> Frame:
        """Send one request, starting a child if needed, and wait within the supplied timeout.

        Timeouts and broken pipes terminate this child. A later request starts its replacement;
        this request is not replayed.
        """
        try:
            with anyio.fail_after(timeout):
                async with self._lock:
                    process = await self._ensure_started()
                    self._next_id += 1
                    # `id` counts per worker, not per pool, so the correlation key in the logs is the
                    # pair (worker_pid, request_id).
                    header: dict[str, Any] = {"id": self._next_id, "op": operation, "params": params}
                    job_id = current_schematic_job_id.get()
                    if job_id is not None:
                        header["job_id"] = job_id
                    if trace := trace_context_headers():
                        header["trace"] = trace
                    frame = Frame(header, tuple(payloads))
                    assert process.stdin is not None and process.stdout is not None
                    process.stdin.write(frame.encode())
                    await process.stdin.drain()
                    response = await wire.read_frame(process.stdout)
                    wire.validate_worker_response(response, request_id=self._next_id, operation=operation)
        except TimeoutError:
            exit_code = await self._cleanup_failed_worker()
            _record_worker_failure(exit_code, reason="timeout")
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout) from None
        except FrameStreamClosed, ConnectionResetError, BrokenPipeError:
            exit_code = await self._cleanup_failed_worker()
            _record_worker_failure(exit_code, reason="crash")
            raise SchematicWorkerCrashedError(operation=operation, exit_code=exit_code) from None
        return response

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process

        restart_delay = self._restart_not_before - asyncio.get_running_loop().time()
        if restart_delay > 0:
            await asyncio.sleep(restart_delay)

        limits = json.dumps(
            {
                "memory_bytes": self._config.worker_memory_limit_bytes,
                "cpu_seconds": self._config.worker_cpu_seconds,
                "file_size_bytes": self._config.worker_file_size_limit_bytes,
            }
        )
        # A new session makes the child a process-group leader, so a timeout can reap the
        # engine's own thread pools and helper processes along with it.
        self._process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            WORKER_MODULE,
            limits,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name == "posix",
            limit=STDERR_LINE_LIMIT,
        )
        if self._started_once:
            add_counter("squid.schematic.worker.respawns")
            logger.warning("Respawning schematic worker as %s after a failure.", self._process.pid)
        self._started_once = True
        self._start_pump(self._process)
        return self._process

    def defer_restart(self, delay: float) -> None:
        """Withhold this worker from respawn without delaying the failed caller."""
        self._restart_not_before = max(self._restart_not_before, asyncio.get_running_loop().time() + delay)

    async def _cleanup_failed_worker(self) -> int | None:
        """Kill immediately, then spend only the documented failure-cleanup budget reaping."""
        with anyio.move_on_after(WORKER_FAILURE_CLEANUP_SECONDS):
            return await self._terminate()
        return None

    def _start_pump(self, process: asyncio.subprocess.Process) -> _StderrPump:
        """Start the stderr reader through the pool and retain its shutdown handles."""
        pump = _StderrPump(anyio.CancelScope(), anyio.Event())
        self._stderr_pump = pump
        self._spawn(functools.partial(self._run_pump, process, pump))
        return pump

    async def _run_pump(self, process: asyncio.subprocess.Process, pump: _StderrPump) -> None:
        """Run one pump inside its own cancel scope, always announcing that it has stopped."""
        with pump.scope:
            try:
                await self._pump_stderr(process)
            finally:
                pump.finished.set()

    async def _pump_stderr(self, process: asyncio.subprocess.Process) -> None:
        """Forward the child's stderr — including faulthandler tracebacks — into our logs."""
        assert process.stderr is not None
        try:
            while True:
                try:
                    line = await process.stderr.readline()
                except ValueError:
                    # `readline` drops the buffered partial line before raising, so the next
                    # read resumes cleanly. Losing one record beats losing the whole stream.
                    worker_logger.warning(
                        "Schematic worker emitted an oversized stderr line; it was dropped.",
                        extra={"worker_pid": process.pid},
                    )
                    continue
                if not line:
                    return
                decoded = line.decode("utf-8", "replace").rstrip()
                record = _worker_log_record(decoded, process.pid)
                if record is None:
                    worker_logger.warning(
                        "Schematic worker emitted unstructured stderr: %s",
                        decoded,
                        extra={"worker_pid": process.pid},
                    )
                else:
                    _emit_child_record(record)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Without this the pump dies silently and the worker's stderr is never forwarded
            # again for the life of the process.
            logger.exception("The stderr pump for schematic worker %s died; further worker logs are lost.", process.pid)

    async def _terminate(self) -> int | None:
        """Kill the child (its process group on POSIX), drain stderr, and return any known exit code."""
        process, self._process = self._process, None
        pump, self._stderr_pump = self._stderr_pump, None
        if process is None:
            if pump is not None:
                pump.scope.cancel()
                await pump.finished.wait()
            return None

        try:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    if os.name == "posix":
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    else:  # pragma: no cover - Windows
                        process.kill()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 5.0)
            # Let the reader consume buffered crash diagnostics before cancelling it.
            if pump is not None:
                await pump.drain(2.0)
        finally:
            if pump is not None and not pump.finished.is_set():
                pump.scope.cancel()
        if process.returncode is not None:
            logger.log(
                logging.INFO if process.returncode == 0 else logging.WARNING,
                "Schematic worker %s exited with code %s.",
                process.pid,
                process.returncode,
            )
        return process.returncode

    async def aclose(self) -> None:
        """Close stdin so the child returns from `serve`, then make sure it is gone."""
        process = self._process
        if process is not None and process.stdin is not None and process.returncode is None:
            with contextlib.suppress(ConnectionResetError, BrokenPipeError, OSError):
                process.stdin.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(process.wait(), 2.0)
        await self._terminate()


def _emit_child_record(record: logging.LogRecord) -> None:
    """Forward a child record through its original logger, honoring the parent's level setting.

    Logger.handle does not check isEnabledFor, so the check must happen before dispatch.
    """
    target = logging.getLogger(record.name)
    if target.isEnabledFor(record.levelno):
        target.handle(record)


def _worker_log_record(line: str, process_id: int) -> logging.LogRecord | None:
    """Rebuild one structured child log record, or reject an unstructured line."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None

    name = payload.get("name")
    level_name = payload.get("levelname")
    message = payload.get("message")
    if not isinstance(name, str) or not isinstance(level_name, str) or not isinstance(message, str):
        return None
    level = logging.getLevelNamesMapping().get(level_name.upper())
    if level is None:
        return None

    pathname = payload.get("pathname")
    lineno = payload.get("lineno")
    record = logging.LogRecord(
        name=name,
        level=level,
        pathname=pathname if isinstance(pathname, str) else "",
        lineno=lineno if isinstance(lineno, int) else 0,
        msg=message,
        args=(),
        exc_info=None,
    )
    created = payload.get("created")
    if isinstance(created, int | float):
        record.created = float(created)
        record.msecs = (record.created - int(record.created)) * 1000

    exception_text = payload.get("exc_info")
    if isinstance(exception_text, str):
        record.exc_text = exception_text

    reserved = record.__dict__.keys()
    for key, value in payload.items():
        if key not in reserved and key != "exc_info":
            setattr(record, key, value)
    record.worker_pid = process_id
    return record


def _record_worker_failure(exit_code: int | None, *, reason: str) -> None:
    """Record a worker death as both a metric and a log line.

    Metrics alone are not a signal: `add_counter` is a no-op when the optional observability
    extra is not installed, which left such a deployment with no way at all to see that its
    workers were crash-looping.
    """
    attributes: dict[str, str | int] = {"squid.worker.failure_reason": reason}
    if exit_code is not None:
        attributes["squid.worker.exit_code"] = exit_code
    add_counter("squid.schematic.worker.crashes", attributes=attributes)
    logger.warning("Schematic worker failed (%s) with exit code %s.", reason, exit_code, extra=dict(attributes))

    rlimit_signal = {
        -value: limit
        for value, limit in (
            (getattr(signal, "SIGXCPU", 0), "cpu"),
            (getattr(signal, "SIGXFSZ", 0), "file_size"),
        )
        if value
    }.get(exit_code)
    if rlimit_signal is not None:
        assert exit_code is not None
        rlimit_attributes = {"squid.worker.rlimit": rlimit_signal, "squid.worker.exit_code": exit_code}
        add_counter("squid.schematic.worker.rlimit_kills", attributes=rlimit_attributes)
        logger.warning(
            "Schematic worker exceeded its %s limit and was killed.", rlimit_signal, extra=dict(rlimit_attributes)
        )


@dataclasses.dataclass(slots=True)
class _CircuitBreaker:
    """Trip after too many worker deaths inside a rolling window."""

    max_failures: int
    window_seconds: float
    _failures: deque[float] = dataclasses.field(default_factory=deque)

    def record_failure(self, now: float) -> None:
        self._failures.append(now)
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()

    def is_open(self, now: float) -> bool:
        while self._failures and now - self._failures[0] > self.window_seconds:
            self._failures.popleft()
        return len(self._failures) >= self.max_failures

    def backoff_seconds(self, base: float) -> float:
        """Grow the pause between restarts, capped so recovery is still possible."""
        return min(base * (2 ** max(len(self._failures) - 1, 0)), 30.0)


class SchematicWorkerPool:
    """An analyzer backed by supervised subprocesses.

    The owner must hold running() open while serving requests so stderr readers have a task group
    that lasts as long as their worker processes.
    """

    def __init__(self, config: SchematicConfig) -> None:
        self._config = config
        self._workers = [_Worker(config, self._spawn_pump) for _ in range(config.workers)]
        self._available = asyncio.Semaphore(config.workers)
        self._render_slot = asyncio.Semaphore(1)
        """One GPU, so renders are serialised regardless of how many workers exist."""
        self._idle: deque[_Worker] = deque(self._workers)
        self._breaker = _CircuitBreaker(config.max_restarts_per_window, config.restart_window_seconds)
        self._breaker_was_open = False
        self._waiting = 0
        self._closed = False
        self._pumps: TaskGroup | None = None

    @asynccontextmanager
    async def running(self) -> AsyncGenerator[Self]:
        """Own stderr reader tasks until the pool and its workers shut down.

        Readers start lazily during requests but outlive those requests. Keep this context open in
        the task that owns the pool, and stop serving requests before leaving it.
        """
        if self._pumps is not None:
            msg = "The schematic worker pool is already running."
            raise RuntimeError(msg)
        async with anyio.create_task_group() as pumps:
            self._pumps = pumps
            try:
                yield self
            finally:
                # Closing workers also stops their stderr readers before the task group exits.
                await self.aclose()
                self._pumps = None

    def _spawn_pump(self, pump: _PumpBody) -> None:
        if self._pumps is None:
            msg = "The schematic worker pool must be entered with `running()` before it serves requests."
            raise RuntimeError(msg)
        self._pumps.start_soon(pump)

    async def record_health(self) -> None:
        """Report idle workers, waiting requests, and circuit-breaker state.

        Waiting for the separate render slot is excluded from these counts and from the operation
        duration metric, which starts after a render acquires that slot.
        """
        idle = len(self._idle)
        record_gauge("squid.schematic.pool.idle_workers", idle)
        record_gauge("squid.schematic.pool.in_flight", len(self._workers) - idle)
        record_gauge("squid.schematic.pool.waiters", self._waiting)
        self._note_breaker_state(asyncio.get_running_loop().time())

    def _note_breaker_state(self, now: float) -> None:
        """Log and publish breaker transitions, once per transition rather than per call.

        Closing again is only noticed the next time the pool is asked to do something or
        sampled for health, because the window slides on inspection rather than on a timer.
        """
        is_open = self._breaker.is_open(now)
        record_gauge("squid.schematic.pool.breaker_open", int(is_open))
        if is_open == self._breaker_was_open:
            return
        self._breaker_was_open = is_open
        if is_open:
            add_counter("squid.schematic.worker.breaker.trips")
            logger.error(
                "Schematic worker circuit breaker opened after %s failures in %ss; "
                "schematic operations are refused until it closes.",
                self._config.max_restarts_per_window,
                self._config.restart_window_seconds,
            )
        else:
            logger.info("Schematic worker circuit breaker closed; schematic operations resume.")

    async def capabilities(self) -> AnalyzerCapabilities:
        try:
            frame = await self._call("capabilities", {}, (), self._config.parse_timeout_seconds)
        except (SchematicWorkerCrashedError, SchematicTimeoutError, SchematicSupportUnavailableError) as exc:
            return AnalyzerCapabilities(available=False, unavailable_reason=exc.public_detail())
        return wire.decode_capabilities(cast(Mapping[str, Any], _result(frame)["capabilities"]))

    async def analyze(
        self,
        data: bytes,
        *,
        limits: SchematicLimits,
        with_lattice: bool = False,
        source_format: SchematicFormat | None = None,
    ) -> SchematicAnalysis:
        params = {
            "limits": dataclasses.asdict(limits),
            "with_lattice": with_lattice,
            "source_format": source_format.value if source_format is not None else None,
            "lattice_max_block_count": self._config.lattice_max_block_count,
        }
        frame = await self._call("analyze", params, (data,), self._config.parse_timeout_seconds)
        return wire.decode_analysis(cast(Mapping[str, Any], _result(frame)["analysis"]))

    async def convert(
        self, data: bytes, *, target: SchematicFormat, data_version: int | None = None
    ) -> tuple[bytes, tuple[VersionLossEntry, ...]]:
        params = {"target": target.value, "data_version": data_version}
        frame = await self._call("convert", params, (data,), self._config.convert_timeout_seconds)
        return _payload(frame), wire.decode_losses(_result(frame)["losses"])

    async def compare(
        self,
        left: bytes,
        right: bytes,
        *,
        preset: FingerprintPreset,
        timeout_seconds: float | None = None,
    ) -> SchematicComparison:
        timeout = self._config.compare_timeout_seconds
        if timeout_seconds is not None:
            timeout = min(timeout, timeout_seconds)
        frame = await self._call("compare", {"preset": preset.value}, (left, right), timeout)
        return wire.decode_comparison(cast(Mapping[str, Any], _result(frame)["comparison"]))

    async def render(
        self, data: bytes, *, request: RenderRequest, resource_pack: VerifiedResourcePack | None = None
    ) -> bytes:
        params: dict[str, Any] = {"request": wire.encode_render_request(request)}
        if resource_pack is not None:
            params["resource_pack_b64"] = base64.b64encode(resource_pack.data).decode("ascii")
            params["resource_pack"] = wire.encode_resource_pack(resource_pack)
        async with self._render_slot:
            frame = await self._call("render", params, (data,), self._config.render_timeout_seconds)
        return _payload(frame)

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        params = {"request": wire.encode_simulation_request(request)}
        frame = await self._call("simulate", params, (data,), self._config.simulate_timeout_seconds)
        return wire.decode_simulation(cast(Mapping[str, Any], _result(frame)["simulation"]))

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        params = {"lattice": wire.encode_lattice(lattice), "counts": list(counts)}
        frame = await self._call("autostack", params, (data,), self._config.convert_timeout_seconds)
        return _payload(frame)

    async def aclose(self) -> None:
        """Shut down every worker; repeated calls are allowed.

        gather(return_exceptions=True) lets all shutdown attempts finish even if one raises. Individual
        shutdown errors are collected and suppressed.
        """
        self._closed = True
        await asyncio.gather(*(worker.aclose() for worker in self._workers), return_exceptions=True)

    async def _call(
        self, operation: Operation, params: Mapping[str, Any], payloads: Sequence[bytes], timeout: float
    ) -> Frame:
        started = asyncio.get_running_loop().time()
        outcome = "ok"
        try:
            return await self._call_unmeasured(operation, params, payloads, timeout)
        except DomainError:
            outcome = "rejected"
            raise
        except Exception:
            outcome = "error"
            raise
        finally:
            record_histogram(
                "squid.schematic.operation.duration",
                asyncio.get_running_loop().time() - started,
                attributes={"squid.schematic.operation": operation, "squid.outcome": outcome},
            )

    async def _call_unmeasured(
        self, operation: Operation, params: Mapping[str, Any], payloads: Sequence[bytes], timeout: float
    ) -> Frame:
        """Lease a worker, run one operation on it, and translate any failure it reports."""
        if self._closed:
            msg = "The schematic worker pool has been shut down."
            raise SchematicSupportUnavailableError(msg)
        now = asyncio.get_running_loop().time()
        self._note_breaker_state(now)
        if self._breaker.is_open(now):
            msg = "The schematic engine is failing repeatedly and has been taken out of service."
            raise SchematicSupportUnavailableError(
                msg, developer_action="Check the squid.schematics logs for the crashing payload."
            )

        self._waiting += 1
        try:
            await asyncio.wait_for(self._available.acquire(), timeout)
        except TimeoutError:
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout) from None
        finally:
            self._waiting -= 1

        leased_at = asyncio.get_running_loop().time()
        remaining = timeout - (leased_at - now)
        if remaining <= 0:
            self._available.release()
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout)

        try:
            worker = self._idle.popleft()
            try:
                frame = await worker.request(operation, params, payloads, remaining)
            except SchematicWorkerCrashedError, SchematicTimeoutError:
                loop_time = asyncio.get_running_loop().time()
                self._breaker.record_failure(loop_time)
                self._note_breaker_state(loop_time)
                # The next caller pays this restart cooldown inside its own deadline; the
                # caller that already timed out returns after only the cleanup budget.
                worker.defer_restart(self._breaker.backoff_seconds(self._config.restart_backoff_seconds))
                raise
            finally:
                self._idle.append(worker)
        finally:
            self._available.release()

        if frame.header["ok"] is False:
            raise _translate(frame.header["error"], operation)
        return frame


def _result(frame: Frame) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], frame.header.get("result", {}))


def _payload(frame: Frame) -> bytes:
    return frame.payloads[0] if frame.payloads else b""


def _translate(error: object, operation: str) -> Exception:
    """Rebuild a typed exception from the child's error kind, message, and context."""
    kind, message, context = wire.decode_error(error)

    if kind == "too_large":
        return SchematicTooLargeError(
            actual=_context_integer(context, "actual"),
            limit=_context_integer(context, "limit"),
            measure=_context_string(context, "measure"),
        )
    if kind == "ambiguous_simulation_input":
        return wire.decode_simulation_input_error(context).with_context(context={"operation": operation})
    if kind == "invalid":
        return InvalidSchematicError(context={**context, "operation": operation})
    if kind == "unavailable":
        return SchematicSupportUnavailableError(context={"operation": operation})
    return InfrastructureError(
        message or "The schematic engine failed.",
        resource="schematic",
        context={**context, "operation": operation},
    )


def _context_integer(context: Mapping[str, Any], key: str) -> int:
    value = context.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"Worker error context {key!r} must be an integer."
        raise TypeError(msg)
    return value


def _context_string(context: Mapping[str, Any], key: str) -> str:
    value = context.get(key)
    if not isinstance(value, str):
        msg = f"Worker error context {key!r} must be a string."
        raise TypeError(msg)
    return value

"""A supervised pool of schematic worker subprocesses.

`asyncio.to_thread` is the right tool for the blocking network calls elsewhere in this
codebase, and the wrong one here. It cannot cancel: `wait_for` returns control to the caller
while the thread keeps running, so one pathological schematic pins a default-executor thread —
an executor shared with the embedding client — for as long as the engine wants it. Worse, the
engine's failures are not always exceptions: wgpu, rayon, and MCHPRS spawn their own threads,
and a Rust panic there can take the whole interpreter down.

`ProcessPoolExecutor` isolates the crash but not the recovery: one dead child raises
`BrokenProcessPool` and poisons every subsequent submission with no respawn. Automatic
per-worker recovery is the entire reason this pool is written by hand.

Safety properties this module is responsible for:

- **One in-flight request per worker**, so a response frame can never be attributed to the
  wrong caller.
- **A deadline on every operation.** On expiry the child's whole process group is killed, not
  just the child, because the engine's thread pools may have spawned helpers.
- **No automatic retry.** A payload that just killed a worker would kill the next one too;
  retrying it is how a crash loop starts.
- **Restart backoff with a circuit breaker**, so a user re-uploading a poison file cannot turn
  the supervisor into a fork bomb.
"""

import asyncio
import base64
import contextlib
import dataclasses
import json
import logging
import os
import signal
import sys
from collections import deque
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from typing import Any, Self, cast

from squid.config import SchematicConfig
from squid.core.errors import DomainError, InfrastructureError
from squid.observability import add_counter, inject_trace_context, record_histogram
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
"""Stands in for a child that told us nothing usable about itself.

Structured child records are re-emitted under their own logger names by `_emit_child_record`;
this one carries only the supervisor's own commentary about a worker's stderr.
"""

WORKER_MODULE = "squid.schematics.infrastructure.worker_main"

current_schematic_job_id: ContextVar[int | None] = ContextVar("squid.schematic_job_id", default=None)
"""The durable job a request belongs to, if it came from one.

Carried in the request frame so the child can stamp it on its own logs. A ContextVar rather
than a parameter because every method of the seven-method analyzer protocol would otherwise
have to grow one, for the benefit of a single implementation.
"""

STDERR_LINE_LIMIT = 1024 * 1024
"""`StreamReader` line budget for the child's stderr.

A faulthandler traceback or a Rust panic dump serialised into one JSON record easily exceeds
asyncio's 64 KiB default, and overrunning it raises `ValueError` out of `readline` — losing
exactly the diagnostics this pipe exists to carry.
"""


class _Worker:
    """One child process and the single request slot that guards it."""

    def __init__(self, config: SchematicConfig) -> None:
        self._config = config
        self._process: asyncio.subprocess.Process | None = None
        self._stderr_pump: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._next_id = 0
        self._started_once = False

    async def request(
        self, operation: Operation, params: Mapping[str, Any], payloads: Sequence[bytes], timeout: float
    ) -> Frame:
        """Send one request and await its response, respawning on timeout or death."""
        async with self._lock:
            process = await self._ensure_started()
            self._next_id += 1
            # `id` counts per worker, not per pool, so the correlation key in the logs is the
            # pair (worker_pid, request_id).
            header: dict[str, Any] = {"id": self._next_id, "op": operation, "params": params}
            job_id = current_schematic_job_id.get()
            if job_id is not None:
                header["job_id"] = job_id
            inject_trace_context(header)
            frame = Frame(header, tuple(payloads))
            assert process.stdin is not None and process.stdout is not None
            try:
                process.stdin.write(frame.encode())
                await asyncio.wait_for(process.stdin.drain(), timeout)
                return await asyncio.wait_for(wire.read_frame(process.stdout), timeout)
            except TimeoutError:
                exit_code = await self._terminate()
                _record_worker_failure(exit_code, reason="timeout")
                raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout) from None
            except (FrameStreamClosed, ConnectionResetError, BrokenPipeError):
                exit_code = await self._terminate()
                _record_worker_failure(exit_code, reason="crash")
                raise SchematicWorkerCrashedError(operation=operation, exit_code=exit_code) from None

    async def _ensure_started(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process

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
        self._started_once = True
        self._stderr_pump = asyncio.create_task(self._pump_stderr(self._process))
        return self._process

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
        """Kill the child and its whole group, returning its exit code if we can learn one."""
        process, self._process = self._process, None
        pump, self._stderr_pump = self._stderr_pump, None
        if process is None:
            if pump is not None:
                pump.cancel()
                # cancel() only schedules the CancelledError; dropping the last strong
                # reference before it lands lets the task be collected mid-cancellation.
                with contextlib.suppress(asyncio.CancelledError):
                    await pump
            return None

        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                if os.name == "posix":
                    os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                else:  # pragma: no cover - Windows
                    process.kill()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), 5.0)
        # Drain after the process is gone, never before: on a crash the traceback we care about
        # is still sitting in the pipe at exactly the moment the old code cancelled the pump.
        if pump is not None:
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(pump, 2.0)
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
    """Dispatch a rebuilt child record as if the child's own logger had emitted it.

    `Logger.handle` skips `isEnabledFor`, so re-emitting through one fixed logger made
    parent-side level configuration for the child's module names inert. Going through the
    record's own logger, with the level check the parent applies to its own records, keeps
    filtering meaningful; the handler chain is unchanged because child loggers are `squid.*`.
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
    attributes: dict[str, str | int] = {"squid.worker.failure_reason": reason}
    if exit_code is not None:
        attributes["squid.worker.exit_code"] = exit_code
    add_counter("squid.schematic.worker.crashes", attributes=attributes)

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
        add_counter(
            "squid.schematic.worker.rlimit_kills",
            attributes={"squid.worker.rlimit": rlimit_signal, "squid.worker.exit_code": exit_code},
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
    """A `SchematicAnalyzer` backed by supervised subprocesses."""

    def __init__(self, config: SchematicConfig) -> None:
        self._config = config
        self._workers = [_Worker(config) for _ in range(config.workers)]
        self._available = asyncio.Semaphore(config.workers)
        self._render_slot = asyncio.Semaphore(1)
        """One GPU, so renders are serialised regardless of how many workers exist."""
        self._idle: deque[_Worker] = deque(self._workers)
        self._breaker = _CircuitBreaker(config.max_restarts_per_window, config.restart_window_seconds)
        self._closed = False

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
        return _payload(frame), wire.decode_losses(_result(frame).get("losses"))

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

    async def render(self, data: bytes, *, request: RenderRequest, resource_pack: bytes | None = None) -> bytes:
        params: dict[str, Any] = {"request": dataclasses.asdict(request)}
        if resource_pack is not None:
            params["resource_pack_b64"] = base64.b64encode(resource_pack).decode("ascii")
        async with self._render_slot:
            frame = await self._call("render", params, (data,), self._config.render_timeout_seconds)
        return _payload(frame)

    async def simulate(self, data: bytes, *, request: SimulationRequest) -> SimulationResult:
        params = {"request": dataclasses.asdict(request)}
        frame = await self._call("simulate", params, (data,), self._config.simulate_timeout_seconds)
        return wire.decode_simulation(cast(Mapping[str, Any], _result(frame)["simulation"]))

    async def autostack(self, data: bytes, *, lattice: AutostackLattice, counts: tuple[int, ...]) -> bytes:
        params = {"lattice": wire.encode_lattice(lattice), "counts": list(counts)}
        frame = await self._call("autostack", params, (data,), self._config.convert_timeout_seconds)
        return _payload(frame)

    async def aclose(self) -> None:
        """Shut every worker down. Safe to call more than once."""
        self._closed = True
        await asyncio.gather(*(worker.aclose() for worker in self._workers), return_exceptions=True)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

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
        if self._breaker.is_open(now):
            msg = "The schematic engine is failing repeatedly and has been taken out of service."
            raise SchematicSupportUnavailableError(
                msg, developer_action="Check the squid.schematics logs for the crashing payload."
            )

        try:
            await asyncio.wait_for(self._available.acquire(), timeout)
        except TimeoutError:
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout) from None

        leased_at = asyncio.get_running_loop().time()
        remaining = timeout - (leased_at - now)
        if remaining <= 0:
            self._available.release()
            raise SchematicTimeoutError(operation=operation, timeout_seconds=timeout)

        try:
            worker = self._idle.popleft()
            try:
                frame = await worker.request(operation, params, payloads, remaining)
            except (SchematicWorkerCrashedError, SchematicTimeoutError):
                loop_time = asyncio.get_running_loop().time()
                self._breaker.record_failure(loop_time)
                # A pause before this worker is next handed out, so a payload a user keeps
                # retrying cannot spawn processes as fast as they can press the button.
                await asyncio.sleep(self._breaker.backoff_seconds(self._config.restart_backoff_seconds))
                raise
            finally:
                self._idle.append(worker)
        finally:
            self._available.release()

        if not frame.header.get("ok", False):
            raise _translate(cast(Mapping[str, Any], frame.header.get("error", {})), operation)
        return frame


def _result(frame: Frame) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], frame.header.get("result", {}))


def _payload(frame: Frame) -> bytes:
    return frame.payloads[0] if frame.payloads else b""


def _translate(error: Mapping[str, Any], operation: str) -> Exception:
    """Rebuild a typed exception from the child's error frame.

    The child cannot ship an exception object across the pipe, so it sends a discriminated
    payload instead and the mapping lives here, in one place.
    """
    kind = error.get("kind", "internal")
    message = str(error.get("message", ""))
    context = cast(Mapping[str, Any], error.get("context", {}))

    if kind == "too_large":
        return SchematicTooLargeError(
            actual=int(context.get("actual", 0)),
            limit=int(context.get("limit", 0)),
            measure=str(context.get("measure", "size")),
        )
    if kind == "invalid":
        return InvalidSchematicError(context={**context, "operation": operation})
    if kind == "unavailable":
        return SchematicSupportUnavailableError(context={"operation": operation})
    return InfrastructureError(
        message or "The schematic engine failed.",
        resource="schematic",
        context={**context, "operation": operation},
    )

# pyright: reportPrivateUsage=false
"""Structured schematic worker logging tests."""

import asyncio
import io
import json
import logging
import os
import signal
import sys
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from typing import Any, NoReturn, Protocol, cast

import anyio
import pytest
from pytest_mock import MockerFixture
from pythonjsonlogger.json import JsonFormatter

from squid.config import LoggingConfig, SchematicConfig
from squid.logging_config import build_logging_config
from squid.schematics.domain.models import AnalyzerCapabilities
from squid.schematics.errors import InvalidSchematicError
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure import worker_main
from squid.schematics.infrastructure.durable import SchematicJobRunner
from squid.schematics.infrastructure.wire import Frame
from squid.schematics.infrastructure.worker import (
    SchematicWorkerPool,
    _emit_child_record,
    _record_worker_failure,
    _Worker,
    _worker_log_record,
    current_schematic_job_id,
)


def unowned_worker(config: SchematicConfig | None = None) -> _Worker:
    """A worker for the tests that drive its pump themselves rather than spawning one."""

    def refuse(_pump: Callable[[], Coroutine[Any, Any, None]]) -> NoReturn:
        raise AssertionError("This test must not spawn an unowned stderr pump.")

    return _Worker(config or SchematicConfig(), refuse)


def test_worker_log_record_preserves_child_identity_and_fields() -> None:
    record = _worker_log_record(
        json.dumps(
            {
                "created": 1234.5,
                "levelname": "DEBUG",
                "name": "squid.schematics.infrastructure.worker_main",
                "message": "Could not lower worker priority.",
                "pathname": "/worker_main.py",
                "lineno": 70,
                "squid.schematic.operation": "render",
                "exc_info": "Traceback (most recent call last): ...",
            }
        ),
        2718,
    )

    assert record is not None
    assert record.levelno == logging.DEBUG
    assert record.name == "squid.schematics.infrastructure.worker_main"
    assert record.getMessage() == "Could not lower worker priority."
    assert record.created == 1234.5
    assert record.exc_text == "Traceback (most recent call last): ..."
    assert getattr(record, "squid.schematic.operation") == "render"
    assert vars(record)["worker_pid"] == 2718


def test_worker_log_record_rejects_native_and_malformed_output() -> None:
    assert _worker_log_record("thread 'rayon-1' panicked at src/lib.rs:42", 2718) is None
    assert _worker_log_record(json.dumps(["not", "a", "record"]), 2718) is None
    assert _worker_log_record(json.dumps({"message": "missing identity"}), 2718) is None


def test_worker_main_owns_observability_after_guardrails(mocker: MockerFixture) -> None:
    events: list[str] = []
    handle = mocker.Mock()

    mocker.patch.object(worker_main, "configure_worker_logging", side_effect=lambda **_: events.append("logging"))
    mocker.patch.object(worker_main, "load_worker_log_config", return_value=mocker.Mock(level="INFO", root_level=None))
    mocker.patch.object(worker_main, "apply_guardrails", side_effect=lambda limits: events.append("guardrails"))
    config = mocker.Mock()
    mocker.patch.object(worker_main, "load_worker_observability_config", return_value=config)
    configure = mocker.patch.object(
        worker_main,
        "configure_observability",
        side_effect=lambda *args, **kwargs: events.append("observability") or handle,
    )
    mocker.patch.object(worker_main, "serve", side_effect=lambda stdin, stdout: events.append("serve"))
    mocker.patch.object(worker_main.sys, "argv", ["worker_main"])
    mocker.patch.object(worker_main.sys, "stdin", mocker.Mock(buffer=io.BytesIO()))
    mocker.patch.object(worker_main.sys, "stdout", mocker.Mock(buffer=io.BytesIO()))

    worker_main.main()

    assert events == ["logging", "guardrails", "observability", "serve"]
    configure.assert_called_once_with(config, service_name="schematic-worker")
    handle.shutdown.assert_called_once_with()


class ChildProcessFactory(Protocol):
    async def __call__(
        self, stderr: bytes = b"", *, returncode: int = 0, stream_limit: int = 65536
    ) -> asyncio.subprocess.Process: ...


@pytest.fixture
async def child_process() -> AsyncIterator[ChildProcessFactory]:
    children: list[asyncio.subprocess.Process] = []

    async def spawn(
        stderr: bytes = b"", *, returncode: int = 0, stream_limit: int = 65536
    ) -> asyncio.subprocess.Process:
        script = f"import sys; sys.stderr.buffer.write({stderr!r}); sys.exit({returncode})"
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            stderr=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            start_new_session=os.name == "posix",
            limit=stream_limit,
        )
        children.append(process)
        return process

    try:
        yield spawn
    finally:
        with anyio.CancelScope(shield=True), anyio.fail_after(5):
            for process in children:
                if process.returncode is None:
                    process.kill()
                if process.stderr is not None and process.stderr.exception() is not None:
                    await process.wait()
                else:
                    await process.communicate()


def child_record(message: str, *, level: str = "INFO") -> bytes:
    return (json.dumps({"levelname": level, "name": "squid.schematics.child", "message": message}) + "\n").encode()


async def test_stderr_pump_reemits_json_and_falls_back_for_native_output(
    child_process: ChildProcessFactory, caplog: pytest.LogCaptureFixture
) -> None:
    process = await child_process(child_record("Structured child record", level="DEBUG") + b"native panic output\n")

    with anyio.fail_after(5), caplog.at_level(logging.DEBUG, logger="squid.schematics"):
        await unowned_worker()._pump_stderr(process)

    structured, fallback = caplog.records
    assert (structured.name, structured.levelno, structured.getMessage()) == (
        "squid.schematics.child",
        logging.DEBUG,
        "Structured child record",
    )
    assert fallback.levelno == logging.WARNING
    assert fallback.getMessage() == "Schematic worker emitted unstructured stderr: native panic output"
    assert all(vars(record)["worker_pid"] == process.pid for record in caplog.records)


async def test_stderr_pump_survives_an_oversized_line(
    child_process: ChildProcessFactory, caplog: pytest.LogCaptureFixture
) -> None:
    process = await child_process(b"x" * 4096 + b"\n" + child_record("still alive"), stream_limit=128)

    with anyio.fail_after(5), caplog.at_level(logging.INFO, logger="squid.schematics"):
        await unowned_worker()._pump_stderr(process)

    assert [record.getMessage() for record in caplog.records] == [
        "Schematic worker emitted an oversized stderr line; it was dropped.",
        "still alive",
    ]


async def test_stderr_pump_logs_when_it_dies(
    child_process: ChildProcessFactory, caplog: pytest.LogCaptureFixture
) -> None:
    process = await child_process()
    with anyio.fail_after(5):
        await process.wait()
    assert process.stderr is not None
    process.stderr.set_exception(RuntimeError("transport gone"))

    with anyio.fail_after(5):
        await unowned_worker()._pump_stderr(process)

    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert "further worker logs are lost" in record.getMessage()
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "transport gone"


@pytest.mark.parametrize(("returncode", "level"), [(0, logging.INFO), (1, logging.WARNING)])
async def test_terminate_drains_buffered_stderr_and_logs_the_exit(
    child_process: ChildProcessFactory, caplog: pytest.LogCaptureFixture, returncode: int, level: int
) -> None:
    process = await child_process(child_record("dying words", level="ERROR"), returncode=returncode)
    with anyio.fail_after(5):
        await process.wait()
    with anyio.fail_after(5), caplog.at_level(logging.INFO, logger="squid.schematics"):
        async with anyio.create_task_group() as pumps:

            def spawn(pump: Callable[[], Coroutine[Any, Any, None]]) -> None:
                pumps.start_soon(pump)

            worker = _Worker(SchematicConfig(), spawn)
            worker._process = process
            worker._start_pump(process)

            assert await worker._terminate() == returncode

    dying, exited = caplog.records
    assert dying.getMessage() == "dying words"
    assert exited.levelno == level
    assert exited.getMessage() == f"Schematic worker {process.pid} exited with code {returncode}."


def test_forwarded_records_reach_the_collector_as_the_child_service() -> None:
    """The collector keys logs off service_name, so the parent must not relabel them."""
    line = json.dumps(
        {
            "levelname": "WARNING",
            "name": "squid.schematics.infrastructure.worker_main",
            "message": "Schematic operation render failed: boom",
            "service_name": "redstone-squid-schematic-worker",
        }
    )
    record = _worker_log_record(line, 2718)
    assert record is not None
    formatter_config = build_logging_config(
        config=LoggingConfig(
            level="INFO", root_level="INFO", directory=Path("/tmp"), log_file=None, access_log_file=None
        ),
        service_name="redstone-squid-worker",
    )["formatters"]
    assert isinstance(formatter_config, dict)
    formatter = JsonFormatter(formatter_config["json"]["format"], defaults=formatter_config["json"]["defaults"])

    payload = json.loads(formatter.format(record))

    assert payload["service_name"] == "redstone-squid-schematic-worker"
    assert payload["worker_pid"] == 2718


def test_child_records_obey_parent_side_levels_for_their_logger(caplog: pytest.LogCaptureFixture) -> None:
    """The point of routing by name: silencing a chatty child module from the parent works."""
    child = logging.getLogger("squid.schematics.child")
    record = _worker_log_record(json.dumps({"levelname": "DEBUG", "name": child.name, "message": "noisy detail"}), 2718)
    assert record is not None
    previous = child.level
    child.setLevel(logging.WARNING)
    try:
        with caplog.at_level(logging.DEBUG):
            _emit_child_record(record)
    finally:
        child.setLevel(previous)

    assert caplog.records == []


async def test_a_pool_that_was_never_entered_refuses_to_start_a_worker() -> None:
    """The pump would otherwise be an unowned task, which is what `running()` exists to stop."""
    pool = SchematicWorkerPool(SchematicConfig(workers=1))
    process = object()

    with pytest.raises(RuntimeError, match="running"):
        pool._workers[0]._start_pump(cast(asyncio.subprocess.Process, process))


async def test_a_pool_cannot_be_entered_twice() -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1))

    async with pool.running():
        with pytest.raises(RuntimeError, match="already running"):
            async with pool.running():
                pass  # pragma: no cover - the second entry must not succeed


async def test_leaving_the_pool_lifetime_stops_every_stderr_pump(mocker: MockerFixture) -> None:
    """A pump outliving its pool would hold a dead child's pipe open for the whole process.

    The stream here is never fed, so the pump is parked in `readline` and can only end by
    being cancelled -- which is the pool's shutdown doing its job, not the pipe closing.
    """
    process = mocker.Mock(stderr=asyncio.StreamReader(), pid=2718, returncode=None)
    pool = SchematicWorkerPool(SchematicConfig(workers=1))
    worker = pool._workers[0]

    with anyio.fail_after(5):
        async with pool.running():
            pump = worker._start_pump(process)
            assert not pump.finished.is_set()

    assert pump.finished.is_set()


async def test_worker_request_injects_trace_context_into_frame(mocker: MockerFixture) -> None:
    worker = unowned_worker()
    stdout = asyncio.StreamReader()
    stdout.feed_data(Frame({"id": 1, "ok": True, "result": {}}).encode())
    stdin = mocker.Mock()
    stdin.drain = mocker.AsyncMock()
    process = mocker.Mock(stdin=stdin, stdout=stdout)
    mocker.patch.object(worker, "_ensure_started", new=mocker.AsyncMock(return_value=process))
    headers = mocker.patch.object(
        worker_module,
        "trace_context_headers",
        return_value={"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
    )

    await worker.request("capabilities", {}, (), 1.0)

    headers.assert_called_once_with()
    encoded = stdin.write.call_args.args[0]
    assert b'"trace":{"traceparent":"00-' in encoded
    assert b'"params":{},"traceparent"' not in encoded


@pytest.mark.parametrize("job_id", [4242, None])
async def test_worker_request_carries_the_durable_job_id(mocker: MockerFixture, job_id: int | None) -> None:
    worker = unowned_worker()
    stdout = asyncio.StreamReader()
    stdout.feed_data(Frame({"id": 1, "ok": True, "result": {}}).encode())
    stdin = mocker.Mock()
    stdin.drain = mocker.AsyncMock()
    mocker.patch.object(
        worker, "_ensure_started", new=mocker.AsyncMock(return_value=mocker.Mock(stdin=stdin, stdout=stdout))
    )
    token = current_schematic_job_id.set(job_id)
    try:
        await worker.request("capabilities", {}, (), 1.0)
    finally:
        current_schematic_job_id.reset(token)

    encoded = stdin.write.call_args.args[0]
    assert (b'"job_id":4242' in encoded) is (job_id is not None)


def test_serve_stamps_request_context_on_child_logs(mocker: MockerFixture, caplog: pytest.LogCaptureFixture) -> None:
    """Without this the only correlation between a child log and its job is the trace context."""
    header = {"id": 7, "op": "analyze", "params": {}, "job_id": 4242}
    stdin = io.BytesIO(Frame(header, (b"schematic",)).encode())

    def _fail(*_: object, **__: object) -> None:
        raise InvalidSchematicError(context={"reason": "unreadable"})

    mocker.patch.object(worker_main, "handle", side_effect=_fail)

    with caplog.at_level(logging.DEBUG, logger=worker_main.logger.name):
        # caplog's handler is not the child's stderr handler, so apply the filter by hand.
        caplog.handler.addFilter(worker_main._request_context)
        try:
            worker_main.serve(stdin, io.BytesIO())
        finally:
            caplog.handler.removeFilter(worker_main._request_context)

    (record,) = caplog.records
    assert getattr(record, "squid.schematic.request_id") == 7
    assert getattr(record, "squid.schematic.job_id") == 4242
    assert getattr(record, "squid.schematic.operation") == "analyze"
    assert worker_main._request_context.fields == {}


async def test_job_runner_publishes_the_job_id_to_the_worker(mocker: MockerFixture) -> None:
    seen: list[int | None] = []

    async def _capture(*_: object, **__: object) -> AnalyzerCapabilities:
        seen.append(current_schematic_job_id.get())
        return AnalyzerCapabilities(available=True)

    analyzer = mocker.Mock()
    analyzer.capabilities = _capture
    jobs = mocker.Mock()
    jobs.complete = mocker.AsyncMock(return_value=True)
    runner = SchematicJobRunner(jobs, mocker.Mock(), analyzer, SchematicConfig())
    job = mocker.Mock(id=4242, operation="capabilities", input_keys=(), params={})

    await runner._process(job)

    assert seen == [4242]
    assert current_schematic_job_id.get() is None


def test_worker_main_extracts_parent_context_around_operation(mocker: MockerFixture) -> None:
    header = {
        "id": 1,
        "op": "analyze",
        "params": {"source_format": "litematic"},
        "trace": {"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"},
    }
    stdin = io.BytesIO(Frame(header, (b"schematic",)).encode())
    stdout = io.BytesIO()
    mocker.patch.object(worker_main, "handle", return_value=({"analysis": {}}, b""))
    context = mocker.MagicMock()
    extract = mocker.patch.object(worker_main, "extracted_trace_span", return_value=context)

    worker_main.serve(stdin, stdout)

    extract.assert_called_once_with(
        "schematic.worker analyze",
        header["trace"],
        {"squid.schematic.operation": "analyze", "squid.schematic.format": "litematic"},
    )


def test_attributable_rlimit_exit_records_crash_and_rlimit_metrics(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    sigxcpu = getattr(signal, "SIGXCPU", None)
    if sigxcpu is None:
        pytest.skip("CPU rlimit signals are POSIX-only")
    add = mocker.patch.object(worker_module, "add_counter")

    with caplog.at_level(logging.WARNING, logger=worker_module.logger.name):
        _record_worker_failure(-sigxcpu, reason="crash")  # pyright: ignore[reportPrivateUsage]

    # Metrics are a no-op without the observability extra, so the logs carry the same facts.
    messages = [record.getMessage() for record in caplog.records]
    assert any("Schematic worker failed (crash)" in message for message in messages)
    assert any("exceeded its cpu limit" in message for message in messages)

    assert add.call_args_list == [
        mocker.call(
            "squid.schematic.worker.crashes",
            attributes={"squid.worker.failure_reason": "crash", "squid.worker.exit_code": -sigxcpu},
        ),
        mocker.call(
            "squid.schematic.worker.rlimit_kills",
            attributes={"squid.worker.rlimit": "cpu", "squid.worker.exit_code": -sigxcpu},
        ),
    ]


async def test_breaker_transitions_are_logged_once_each(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture
) -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1, max_restarts_per_window=1))
    add = mocker.patch.object(worker_module, "add_counter")
    mocker.patch.object(worker_module, "record_gauge")
    now = asyncio.get_running_loop().time()

    with caplog.at_level(logging.INFO, logger=worker_module.logger.name):
        pool._breaker.record_failure(now)
        pool._note_breaker_state(now)
        pool._note_breaker_state(now)  # A second rejected call must not re-announce the trip.
        opened = [record for record in caplog.records if record.levelno == logging.ERROR]

        # Sliding the window past the failure closes the breaker again.
        pool._note_breaker_state(now + SchematicConfig().restart_window_seconds + 1)

    assert len(opened) == 1
    assert add.call_args_list == [mocker.call("squid.schematic.worker.breaker.trips")]
    assert [record.getMessage() for record in caplog.records if record.levelno == logging.INFO] == [
        "Schematic worker circuit breaker closed; schematic operations resume."
    ]


async def test_pool_records_operation_duration_and_outcome(mocker: MockerFixture) -> None:
    pool = SchematicWorkerPool(SchematicConfig(workers=1))
    mocker.patch.object(
        pool,
        "_call_unmeasured",
        new=mocker.AsyncMock(return_value=Frame({"ok": True})),
    )
    histogram = mocker.patch.object(worker_module, "record_histogram")

    await pool._call("capabilities", {}, (), 1.0)  # pyright: ignore[reportPrivateUsage]

    histogram.assert_called_once()
    assert histogram.call_args.args[0] == "squid.schematic.operation.duration"
    assert histogram.call_args.kwargs["attributes"] == {
        "squid.schematic.operation": "capabilities",
        "squid.outcome": "ok",
    }

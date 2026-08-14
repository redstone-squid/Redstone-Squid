# pyright: reportPrivateUsage=false
"""Structured schematic worker logging tests."""

import asyncio
import io
import json
import logging
import signal
from pathlib import Path

import pytest
from pytest_mock import MockerFixture
from pythonjsonlogger.json import JsonFormatter

from squid.config import LoggingConfig, SchematicConfig
from squid.logging_config import build_logging_config
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure import worker_main
from squid.schematics.infrastructure.wire import Frame
from squid.schematics.infrastructure.worker import (
    SchematicWorkerPool,
    _emit_child_record,
    _record_worker_failure,
    _Worker,
    _worker_log_record,
)


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


async def test_stderr_pump_reemits_json_and_falls_back_for_native_output(mocker: MockerFixture) -> None:
    stream = asyncio.StreamReader()
    stream.feed_data(
        (
            json.dumps(
                {
                    "levelname": "DEBUG",
                    "name": "squid.schematics.infrastructure.worker_main",
                    "message": "Structured child record",
                }
            )
            + "\n"
        ).encode()
    )
    stream.feed_data(b"native panic output\n")
    stream.feed_eof()
    process = mocker.Mock()
    process.stderr = stream
    process.pid = 2718
    emit = mocker.patch.object(worker_module, "_emit_child_record")
    warning = mocker.patch.object(worker_module.worker_logger, "warning")

    await _Worker(SchematicConfig())._pump_stderr(process)  # pyright: ignore[reportPrivateUsage]

    record = emit.call_args.args[0]
    assert record.levelno == logging.DEBUG
    assert record.name == "squid.schematics.infrastructure.worker_main"
    warning.assert_called_once_with(
        "Schematic worker emitted unstructured stderr: %s",
        "native panic output",
        extra={"worker_pid": 2718},
    )


async def test_stderr_pump_survives_an_oversized_line(mocker: MockerFixture) -> None:
    stream = asyncio.StreamReader(limit=64)
    stream.feed_data(b"x" * 4096 + b"\n")
    stream.feed_data((json.dumps({"levelname": "INFO", "name": "child", "message": "still alive"}) + "\n").encode())
    stream.feed_eof()
    process = mocker.Mock(stderr=stream, pid=2718)
    emit = mocker.patch.object(worker_module, "_emit_child_record")
    warning = mocker.patch.object(worker_module.worker_logger, "warning")

    await _Worker(SchematicConfig())._pump_stderr(process)

    warning.assert_called_once_with(
        "Schematic worker emitted an oversized stderr line; it was dropped.",
        extra={"worker_pid": 2718},
    )
    assert emit.call_args.args[0].getMessage() == "still alive"


async def test_stderr_pump_logs_when_it_dies(mocker: MockerFixture) -> None:
    stderr = mocker.Mock()
    stderr.readline = mocker.AsyncMock(side_effect=RuntimeError("transport gone"))
    process = mocker.Mock(stderr=stderr, pid=2718)
    exception = mocker.patch.object(worker_module.logger, "exception")

    await _Worker(SchematicConfig())._pump_stderr(process)

    exception.assert_called_once()
    assert "further worker logs are lost" in exception.call_args.args[0]


async def test_terminate_drains_buffered_stderr_before_finishing(mocker: MockerFixture) -> None:
    stream = asyncio.StreamReader()
    stream.feed_data((json.dumps({"levelname": "ERROR", "name": "child", "message": "dying words"}) + "\n").encode())
    stream.feed_eof()
    process = mocker.Mock(stderr=stream, pid=2718, returncode=0)
    process.wait = mocker.AsyncMock(return_value=0)
    emit = mocker.patch.object(worker_module, "_emit_child_record")
    worker = _Worker(SchematicConfig())
    worker._process = process
    worker._stderr_pump = asyncio.create_task(worker._pump_stderr(process))

    await worker._terminate()

    assert emit.call_args.args[0].getMessage() == "dying words"


@pytest.mark.parametrize(("returncode", "level"), [(0, logging.INFO), (-9, logging.WARNING)])
async def test_terminate_logs_expected_exits_below_warning(
    mocker: MockerFixture, caplog: pytest.LogCaptureFixture, returncode: int, level: int
) -> None:
    process = mocker.Mock(stderr=None, pid=2718, returncode=returncode)
    process.wait = mocker.AsyncMock(return_value=returncode)
    worker = _Worker(SchematicConfig())
    worker._process = process

    with caplog.at_level(logging.INFO, logger=worker_module.logger.name):
        await worker._terminate()

    exits = [record for record in caplog.records if "exited with code" in record.getMessage()]
    assert [record.levelno for record in exits] == [level]


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


def test_child_records_are_dispatched_under_their_own_logger(caplog: pytest.LogCaptureFixture) -> None:
    record = _worker_log_record(
        json.dumps({"levelname": "WARNING", "name": "squid.schematics.child", "message": "engine complained"}), 2718
    )
    assert record is not None

    with caplog.at_level(logging.DEBUG):
        _emit_child_record(record)

    assert [(entry.name, entry.getMessage()) for entry in caplog.records] == [
        ("squid.schematics.child", "engine complained")
    ]


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


async def test_worker_request_injects_trace_context_into_frame(mocker: MockerFixture) -> None:
    worker = _Worker(SchematicConfig())  # pyright: ignore[reportPrivateUsage]
    stdout = asyncio.StreamReader()
    stdout.feed_data(Frame({"id": 1, "ok": True}).encode())
    stdin = mocker.Mock()
    stdin.drain = mocker.AsyncMock()
    process = mocker.Mock(stdin=stdin, stdout=stdout)
    mocker.patch.object(worker, "_ensure_started", new=mocker.AsyncMock(return_value=process))
    inject = mocker.patch.object(
        worker_module,
        "inject_trace_context",
        side_effect=lambda header: header.update({"traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01"}),
    )

    await worker.request("capabilities", {}, (), 1.0)

    inject.assert_called_once()
    encoded = stdin.write.call_args.args[0]
    assert b'"traceparent":"00-' in encoded


def test_worker_main_extracts_parent_context_around_operation(mocker: MockerFixture) -> None:
    header = {
        "id": 1,
        "op": "analyze",
        "params": {"source_format": "litematic"},
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    }
    stdin = io.BytesIO(Frame(header).encode())
    stdout = io.BytesIO()
    mocker.patch.object(worker_main, "handle", return_value=({"analysis": {}}, b""))
    context = mocker.MagicMock()
    extract = mocker.patch.object(worker_main, "extracted_trace_span", return_value=context)

    worker_main.serve(stdin, stdout)

    extract.assert_called_once_with(
        "schematic.worker analyze",
        mocker.ANY,
        {"squid.schematic.operation": "analyze", "squid.schematic.format": "litematic"},
    )


def test_attributable_rlimit_exit_records_crash_and_rlimit_metrics(mocker: MockerFixture) -> None:
    sigxcpu = getattr(signal, "SIGXCPU", None)
    if sigxcpu is None:
        pytest.skip("CPU rlimit signals are POSIX-only")
    add = mocker.patch.object(worker_module, "add_counter")

    _record_worker_failure(-sigxcpu, reason="crash")  # pyright: ignore[reportPrivateUsage]

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

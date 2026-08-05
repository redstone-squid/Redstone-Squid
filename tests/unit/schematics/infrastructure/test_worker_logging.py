# pyright: reportPrivateUsage=false
"""Structured schematic worker logging tests."""

import asyncio
import io
import json
import logging
import signal

import pytest
from pytest_mock import MockerFixture

from squid.config import SchematicConfig
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure import worker_main
from squid.schematics.infrastructure.wire import Frame
from squid.schematics.infrastructure.worker import (
    SchematicWorkerPool,
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

    mocker.patch.object(worker_main, "configure_worker_logging", side_effect=lambda: events.append("logging"))
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
    configure.assert_called_once_with(config, service_name="worker")
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
    handle = mocker.patch.object(worker_module.worker_logger, "handle")
    warning = mocker.patch.object(worker_module.worker_logger, "warning")

    await _Worker(SchematicConfig())._pump_stderr(process)  # pyright: ignore[reportPrivateUsage]

    record = handle.call_args.args[0]
    assert record.levelno == logging.DEBUG
    assert record.name == "squid.schematics.infrastructure.worker_main"
    warning.assert_called_once_with("[pid %s] %s", 2718, "native panic output")


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

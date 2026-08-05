"""Structured schematic worker logging tests."""

import asyncio
import json
import logging

from pytest_mock import MockerFixture

from squid.config import SchematicConfig
from squid.schematics.infrastructure import worker as worker_module
from squid.schematics.infrastructure.worker import _Worker, _worker_log_record  # pyright: ignore[reportPrivateUsage]


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

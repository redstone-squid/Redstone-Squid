"""Shared API, Discord, and worker process lifecycle contract."""

import inspect
from dataclasses import dataclass
from types import ModuleType
from typing import Any

import pytest
from pytest_mock import MockerFixture

from squid.api import app as api_app
from squid.bot import app as bot_app
from squid.worker import app as worker_app


@dataclass(frozen=True, slots=True)
class ProcessCase:
    name: str
    module: ModuleType
    body_name: str
    logging_name: str
    service_name: str
    asynchronous: bool
    has_log_listener: bool = False


PROCESS_CASES = (
    ProcessCase("api", api_app, "_run_api", "configure_api_logging", "api", asynchronous=False),
    ProcessCase(
        "bot",
        bot_app,
        "_run_bot",
        "configure_bot_logging",
        "bot",
        asynchronous=True,
        has_log_listener=True,
    ),
    ProcessCase(
        "worker",
        worker_app,
        "_run_worker",
        "configure_service_worker_logging",
        "worker",
        asynchronous=True,
    ),
)


@pytest.mark.parametrize("case", PROCESS_CASES, ids=lambda case: case.name)
@pytest.mark.parametrize("body_fails", [False, True], ids=["success", "failure"])
async def test_process_entry_points_own_telemetry_and_logging_cleanup(
    case: ProcessCase,
    body_fails: bool,
    mocker: MockerFixture,
) -> None:
    events: list[str] = []
    config = mocker.Mock()
    listener = mocker.Mock()
    listener.stop.side_effect = lambda: events.append("listener")
    logging_configure = mocker.patch.object(
        case.module,
        case.logging_name,
        return_value=listener if case.has_log_listener else None,
    )
    handle = mocker.Mock()
    handle.shutdown.side_effect = lambda: events.append("telemetry")

    def configure_observability(*args: Any, **kwargs: Any) -> object:
        del args, kwargs
        events.append("configure")
        return handle

    configure = mocker.patch.object(case.module, "configure_observability", side_effect=configure_observability)

    def run_body(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        events.append("body")
        if body_fails:
            raise RuntimeError("process body failed")

    body = mocker.AsyncMock(side_effect=run_body) if case.asynchronous else mocker.Mock(side_effect=run_body)
    mocker.patch.object(case.module, case.body_name, new=body)

    async def invoke() -> None:
        result = case.module.main(config)
        if inspect.isawaitable(result):
            await result

    if body_fails:
        with pytest.raises(RuntimeError, match="process body failed"):
            await invoke()
    else:
        await invoke()

    logging_configure.assert_called_once_with(config.logging, dev_mode=config.development_mode)
    configure.assert_called_once_with(config.observability, service_name=case.service_name)
    if case.asynchronous:
        body.assert_awaited_once()
    else:
        body.assert_called_once()
    assert body.call_args.args[0] is config
    expected_events = ["configure", "body", "telemetry"]
    if case.has_log_listener:
        expected_events.append("listener")
    assert events == expected_events
    if case.has_log_listener:
        listener.stop.assert_called_once_with()
    else:
        listener.stop.assert_not_called()

"""The checked bot and worker service graphs reject the drift an unchecked double would hide."""

from typing import override

import pytest

from squid.diagnostics.application import ErrorReportService
from squid.runtime import BotServices, WorkerServices
from tests.support.services import bot_services, is_configured, worker_services


class _Reports(ErrorReportService):
    def __init__(self) -> None:
        pass


def test_empty_graphs_leave_services_unconfigured_and_optional_collaborators_absent() -> None:
    bot = bot_services()
    worker = worker_services()

    assert isinstance(bot, BotServices)
    assert bot.schematic_summaries is None
    assert not any(
        is_configured(getattr(bot, field))
        for field in BotServices.__dataclass_fields__
        if field != "schematic_summaries"
    )
    assert isinstance(worker, WorkerServices)
    assert worker.media_runner is None


def test_a_double_that_is_not_the_declared_service_is_rejected() -> None:
    class Impostor:
        async def record(self, error: BaseException) -> None:
            del error

    with pytest.raises(TypeError, match=r"error_reports needs a ErrorReportService, got Impostor"):
        bot_services(error_reports=Impostor())


def test_a_double_inventing_a_method_production_lacks_is_rejected() -> None:
    class Stale(_Reports):
        def content(self) -> bytes:
            return b""

    with pytest.raises(TypeError, match=r"Stale defines \['content'\]"):
        worker_services(error_reports=Stale())


def test_subclasses_are_accepted_as_configured() -> None:
    class Narrowed(_Reports):
        @override
        def __repr__(self) -> str:
            return "Narrowed"

    assert is_configured(bot_services(error_reports=Narrowed()).error_reports)


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(TypeError, match=r"BotServices has no fields \['api_keys'\]"):
        bot_services(api_keys=object())


def test_using_an_unconfigured_service_fails_naming_it() -> None:
    reports = bot_services().error_reports

    with pytest.raises(AssertionError, match=r"did not configure ErrorReportService; it read \.record"):
        _ = reports.record


async def test_an_unconfigured_maintenance_job_fails_when_run() -> None:
    with pytest.raises(AssertionError, match="did not configure this worker maintenance job"):
        await worker_services().purge_idempotency()


def test_an_unconfigured_service_still_answers_isinstance() -> None:
    reports = bot_services().error_reports

    assert isinstance(reports, ErrorReportService)

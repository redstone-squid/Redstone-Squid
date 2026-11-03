"""Capture unexpected failures and resolve the references users quote back."""

import logging
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from whenever import Instant

from squid.core.errors import InvalidStateError, JSONValue, SquidError
from squid.core.i18n import tr
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH, ErrorReportNotFoundError
from squid.diagnostics.models import ErrorReport
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


class ErrorReportRepository(Protocol):
    """Persistence required to store a report and find it again."""

    async def save(self, report: ErrorReport) -> None: ...

    async def find(self, reference: str, *, now: Instant) -> ErrorReport | None: ...

    async def count_matching(self, reference: str, *, now: Instant) -> int: ...

    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]: ...

    async def purge_expired(self, *, now: Instant) -> int: ...

    async def clear_all(self) -> int: ...


class ErrorReportUnitOfWork(UnitOfWork, Protocol):
    @property
    def error_reports(self) -> ErrorReportRepository: ...


class ErrorReportService:
    """Store what failed, and hand it back to whoever is allowed to ask.

    Capture is best effort by construction: `record` never raises, because every one of its
    callers is an error handler that has already failed once and still owes the user a response.
    A report that cannot be written is a lost diagnostic; a report that raises is a command that
    silently does nothing.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory[ErrorReportUnitOfWork],
        retention_hours: int = 168,
        max_traceback_chars: int = 20000,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if retention_hours < 1:
            msg = tr(t"Error report retention must be at least one hour.")
            raise InvalidStateError(msg)
        self._uow_factory = uow_factory
        self._retention_hours = retention_hours
        self._max_traceback_chars = max_traceback_chars
        self._now = now

    async def record(
        self,
        error: BaseException,
        *,
        correlation_id: str,
        reference: str,
        surface: str,
        origin: str | None = None,
        context: Mapping[str, JSONValue] | None = None,
        log_tail: Sequence[str] = (),
        work_lost: bool = False,
    ) -> None:
        """Store one failure, swallowing anything that goes wrong while storing it."""
        try:
            now = self._now()
            report = ErrorReport(
                id=uuid4(),
                correlation_id=correlation_id,
                reference=reference,
                occurred_at=now,
                expires_at=now.add(hours=self._retention_hours),
                surface=surface,
                origin=origin,
                exception_type=type(error).__qualname__,
                message=str(error),
                error_code=error.code if isinstance(error, SquidError) else None,
                traceback=self._format_traceback(error),
                context=dict(context or {}),
                log_tail=list(log_tail),
                work_lost=work_lost,
            )
            async with self._uow_factory() as uow:
                await uow.error_reports.save(report)
                await uow.commit()
        except Exception:
            logger.exception("Could not store an error report [correlation_id=%s]", correlation_id)

    async def lookup(self, reference: str) -> tuple[ErrorReport, int]:
        """Resolve a quoted reference to its report and how many share it.

        The count is returned rather than hidden because the short reference is a 48-bit prefix,
        not a key: a second match is astronomically unlikely but not impossible, and a moderator
        reading a traceback deserves to know they may be reading the wrong one.
        """
        async with self._uow_factory() as uow:
            normalized = self.normalize(reference)
            now = self._now()
            report = await uow.error_reports.find(normalized, now=now)
            if report is None:
                raise ErrorReportNotFoundError(context={"reference": normalized})
            matches = await uow.error_reports.count_matching(normalized, now=now)
            return report, matches

    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        """List the newest unexpired reports, for looking around without a reference.

        `work_lost_only` narrows to failures that abandoned work. Following the logs means most
        reports are failures something recovered from, and those can bury the ones that cost
        something.
        """
        async with self._uow_factory() as uow:
            return await uow.error_reports.list_recent(
                limit=max(1, limit), now=self._now(), work_lost_only=work_lost_only
            )

    async def purge_expired(self) -> int:
        """Delete reports past their retention window."""
        async with self._uow_factory() as uow:
            result = await uow.error_reports.purge_expired(now=self._now())
            await uow.commit()
            return result

    async def clear_all(self) -> int:
        """Delete every stored report, expired or not.

        Unlike `purge_expired`, this is an operator action rather than routine housekeeping, so
        it is gated on `diagnostics.error.clear` rather than running unattended.
        """
        async with self._uow_factory() as uow:
            result = await uow.error_reports.clear_all()
            await uow.commit()
            return result

    @staticmethod
    def normalize(reference: str) -> str:
        """Trim and bound a caller-supplied reference before it reaches a query.

        Users paste references out of Discord, so they arrive wrapped in the backticks the error
        card rendered them in as often as not.
        """
        normalized = reference.strip().strip("`").strip()
        if not normalized:
            raise ErrorReportNotFoundError(context={"reference": reference[:MAX_REFERENCE_LENGTH]})
        return normalized[:MAX_REFERENCE_LENGTH]

    def _format_traceback(self, error: BaseException) -> str:
        """Render a traceback, keeping its tail when it has to be cut.

        The frames nearest the failure are the ones worth reading, and a runaway recursion makes
        the head thousands of identical lines, so the truncation drops from the front.
        """
        rendered = "".join(traceback.format_exception(error))
        if len(rendered) <= self._max_traceback_chars:
            return rendered
        return "...\n" + rendered[-self._max_traceback_chars :]

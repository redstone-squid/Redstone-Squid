"""Capture unexpected failures and resolve the references users quote back."""

import logging
import traceback
from collections.abc import Callable, Mapping, Sequence
from typing import Protocol
from uuid import uuid4

from whenever import Instant

from squid.core.errors import InvalidStateError, JSONValue, SquidError
from squid.core.i18n import tr
from squid.diagnostics.domain import MAX_REFERENCE_LENGTH, ErrorReport, ErrorReportNotFoundError

logger = logging.getLogger(__name__)


class ErrorReportRepository(Protocol):
    """Persistence required to store a report and find it again."""

    async def save(self, report: ErrorReport) -> None:
        """Insert the report. References are not unique, so a repeated one is stored alongside its twin."""
        ...

    async def find(self, reference: str, *, now: Instant) -> ErrorReport | None:
        """The newest unexpired report whose short reference or full correlation ID is `reference`."""
        ...

    async def count_matching(self, reference: str, *, now: Instant) -> int:
        """How many unexpired reports `find` had to choose between."""
        ...

    async def list_recent(self, *, limit: int, now: Instant, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        """Unexpired reports, newest first, optionally only those that abandoned work."""
        ...

    async def purge_expired(self, *, now: Instant) -> int:
        """Delete reports whose retention window has passed, returning how many went."""
        ...

    async def clear_all(self) -> int:
        """Delete every report, expired or not, returning how many went."""
        ...


class ErrorReportService:
    """Store what failed, and hand it back to whoever is allowed to ask.

    `record` never raises: its callers are error handlers that have already failed once and still owe the user a
    response. Construction raises `InvalidStateError` if `retention_hours` is below one.
    """

    def __init__(
        self,
        repository: ErrorReportRepository,
        *,
        retention_hours: int = 168,
        max_traceback_chars: int = 20000,
        now: Callable[[], Instant] = Instant.now,
    ) -> None:
        if retention_hours < 1:
            msg = tr(t"Error report retention must be at least one hour.")
            raise InvalidStateError(msg)
        self._repository = repository
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
                log_tail=tuple(log_tail),
                work_lost=work_lost,
            )
            await self._repository.save(report)
        except Exception:
            logger.exception("Could not store an error report [correlation_id=%s]", correlation_id)

    async def lookup(self, reference: str) -> tuple[ErrorReport, int]:
        """Resolve a quoted reference to its report and how many unexpired reports share it.

        The short reference is a 48-bit prefix rather than a key, so a reader of the traceback needs to know
        whether it could be the wrong one. Raises `ErrorReportNotFoundError` if nothing matches.
        """
        normalized = self.normalize(reference)
        now = self._now()
        report = await self._repository.find(normalized, now=now)
        if report is None:
            raise ErrorReportNotFoundError(context={"reference": normalized})
        matches = await self._repository.count_matching(normalized, now=now)
        return report, matches

    async def recent(self, *, limit: int = 20, work_lost_only: bool = False) -> Sequence[ErrorReport]:
        """List the newest unexpired reports, for looking around without a reference.

        `work_lost_only` narrows to failures that abandoned work, which the far more numerous recovered failures
        otherwise bury.
        """
        return await self._repository.list_recent(limit=max(1, limit), now=self._now(), work_lost_only=work_lost_only)

    async def purge_expired(self) -> int:
        """Delete reports past their retention window."""
        return await self._repository.purge_expired(now=self._now())

    async def clear_all(self) -> int:
        """Delete every stored report, expired or not; an operator action gated on `diagnostics.error.clear`."""
        return await self._repository.clear_all()

    @staticmethod
    def normalize(reference: str) -> str:
        """Strip surrounding whitespace and the backticks a Discord error card renders, then bound the length.

        Raises `ErrorReportNotFoundError` if nothing is left.
        """
        normalized = reference.strip().strip("`").strip()
        if not normalized:
            raise ErrorReportNotFoundError(context={"reference": reference[:MAX_REFERENCE_LENGTH]})
        return normalized[:MAX_REFERENCE_LENGTH]

    def _format_traceback(self, error: BaseException) -> str:
        """Render a traceback, dropping from the front when it has to be cut, so the innermost frames survive."""
        rendered = "".join(traceback.format_exception(error))
        if len(rendered) <= self._max_traceback_chars:
            return rendered
        return "...\n" + rendered[-self._max_traceback_chars :]

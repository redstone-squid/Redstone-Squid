"""Published records associated with a catalogue build."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from squid.core.records.domain import RecordCriterion, ResolutionStatus, VersionScope


@dataclass(frozen=True, slots=True)
class BuildRecord:
    record_id: UUID
    title: str
    criterion: RecordCriterion
    version_scope: VersionScope
    status: ResolutionStatus
    since: datetime | None


@dataclass(frozen=True, slots=True)
class BuildRecords:
    held: tuple[BuildRecord, ...] = ()
    former: tuple[BuildRecord, ...] = ()

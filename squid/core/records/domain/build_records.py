"""Published records associated with a catalogue build."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from squid.core.records.domain import RecordCriterion, VersionScope


@dataclass(frozen=True, slots=True)
class BuildRecord:
    """A record a build holds or held; `retired` means no build competes for it any more."""

    record_id: UUID
    title: str
    criterion: RecordCriterion
    version_scope: VersionScope
    since: datetime | None
    retired: bool = False


@dataclass(frozen=True, slots=True)
class BuildRecords:
    held: tuple[BuildRecord, ...] = ()
    former: tuple[BuildRecord, ...] = ()

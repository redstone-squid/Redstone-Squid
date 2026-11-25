"""Domain events published with record results."""

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar
from uuid import UUID


class RecordChangeKind(StrEnum):
    """How one record changed in a published batch.

    `appeared` is a record's first result, `revived` a retired record's new one,
    `holders_changed` a live record whose holders changed, and `retired` a record
    that lost its last competitor.
    """

    APPEARED = "appeared"
    REVIVED = "revived"
    HOLDERS_CHANGED = "holders_changed"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class RecordChange:
    record_id: UUID
    kind: RecordChangeKind
    previous_holder_ids: tuple[int, ...]
    holder_ids: tuple[int, ...]

    def payload(self) -> dict[str, object]:
        return {
            "record_id": str(self.record_id),
            "change": self.kind.value,
            "previous_holder_ids": list(self.previous_holder_ids),
            "holder_ids": list(self.holder_ids),
        }


@dataclass(frozen=True, slots=True)
class RecordRunPublished:
    """A batch changed the holders or liveness of some records.

    Results whose contents changed without a change of holders publish no entry.
    A baseline batch, the first after records are rebuilt from nothing, sends no
    notifications.
    """

    event_type: ClassVar[str] = "record_run.published"
    aggregate_kind: ClassVar[str] = "record_run"
    schema_version: ClassVar[int] = 1

    aggregate_id: int
    build_kind: str
    changes: tuple[RecordChange, ...]
    baseline: bool = False

    def payload(self) -> dict[str, object]:
        return {
            "build_kind": self.build_kind,
            "baseline": self.baseline,
            "changes": [change.payload() for change in self.changes],
        }

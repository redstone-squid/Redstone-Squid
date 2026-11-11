"""Detached copies of a tag assignment. Showcase edits replace an assignment only while it still matches one."""

from dataclasses import dataclass

from whenever import Instant

from squid.tags.domain import TagValue, TagValueType
from squid.tags.models import TagAssignment


@dataclass(frozen=True, slots=True)
class AssignmentSnapshot:
    value_type: TagValueType
    value: TagValue
    display_unit: str | None
    display_order: int | None
    evidence: str | None
    provenance: str
    created_by_account_id: int | None
    created_at: Instant

    @classmethod
    def capture(cls, row: TagAssignment) -> AssignmentSnapshot:
        return cls(
            row.value_type,
            row.value,
            row.display_unit,
            row.display_order,
            row.evidence,
            row.provenance,
            row.created_by_account_id,
            row.created_at,
        )

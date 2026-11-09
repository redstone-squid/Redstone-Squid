"""Internal build history values; external adapters must authorize their readers."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from pydantic import JsonValue
from whenever import Instant

type HistoryOperation = Literal[
    "baseline", "create", "edit", "moderation", "recalculation", "schematic", "account_merge", "delete"
]


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildChange:
    """Attribution for one logical operation, shared by its nested writes.

    Supply exactly one actor. The account identifier is historical and survives
    account merging. System names identify workers, not the build's submitter.
    """

    operation: HistoryOperation
    source: str
    actor_account_id: int | None = None
    actor_system: str | None = None
    correlation_kind: str | None = None
    correlation_id: str | None = None
    operation_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if (self.actor_account_id is None) == (self.actor_system is None):
            msg = "Supply exactly one history actor"
            raise ValueError(msg)
        if (self.correlation_kind is None) != (self.correlation_id is None):
            msg = "Supply both correlation kind and identifier"
            raise ValueError(msg)
        if not self.source.strip() or (self.actor_system is not None and not self.actor_system.strip()):
            msg = "History source and system actor must not be blank"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildHistorySummary:
    """Timeline metadata without the potentially large snapshot payloads."""

    id: int
    build_id: int
    recorded_at: Instant
    change: BuildChange
    before_revision: int | None
    after_revision: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildSnapshot:
    """A detached, versioned historical state, never a live ORM build.

    Decimal tag values are strings so JSON decoding cannot round their precision.
    Timestamps use UTC. Attachment metadata excludes recomputable analysis data.
    """

    schema_version: int
    revision: int
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildHistoryEntry(BuildHistorySummary):
    before: BuildSnapshot | None
    after: BuildSnapshot | None

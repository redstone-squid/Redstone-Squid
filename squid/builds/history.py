"""Internal build history values; external adapters must authorize their readers."""

from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from pydantic import JsonValue
from whenever import Instant

type BuildEditActor = int | str
"""An account ID or a named system actor."""


type HistoryOperation = Literal[
    "baseline", "create", "edit", "moderation", "recalculation", "schematic", "account_merge", "delete"
]


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildEditContext:
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

    @classmethod
    def from_actor(
        cls,
        actor: BuildEditActor,
        *,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> BuildEditContext:
        return cls(
            operation=operation,
            source=source,
            actor_account_id=actor if isinstance(actor, int) else None,
            actor_system=actor if isinstance(actor, str) else None,
            correlation_kind=correlation_kind,
            correlation_id=correlation_id,
        )

    @property
    def actor(self) -> BuildEditActor:
        """Return the account or system actor retained in this entry."""
        if self.actor_account_id is not None:
            return self.actor_account_id
        assert self.actor_system is not None
        return self.actor_system

    def __post_init__(self) -> None:
        if self.actor_account_id is not None and (
            isinstance(self.actor_account_id, bool) or self.actor_account_id <= 0
        ):
            msg = "An account actor must have a positive integer ID"
            raise ValueError(msg)
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
    context: BuildEditContext
    before_revision: int | None
    after_revision: int | None


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildStateSnapshot:
    """A detached, versioned historical state, never a live ORM build.

    Decimal tag values are strings so JSON decoding cannot round their precision.
    Timestamps use UTC. Attachment metadata excludes recomputable analysis data.
    """

    schema_version: int
    revision: int
    payload: dict[str, JsonValue]


@dataclass(frozen=True, slots=True, kw_only=True)
class BuildHistoryDetail(BuildHistorySummary):
    before: BuildStateSnapshot | None
    after: BuildStateSnapshot | None

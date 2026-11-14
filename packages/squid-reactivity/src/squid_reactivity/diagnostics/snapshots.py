"""Portable diagnostic events and the policies that redact their sensitive fields."""

from dataclasses import dataclass, replace
from datetime import datetime

from squid_reactivity.actions import (
    ActionCommit,
    ActionRelation,
    ActionResult,
    ActorRef,
    CausalRef,
    ChangeReport,
    CommitSequence,
    ConflictDetail,
    ExceptionReport,
)


@dataclass(frozen=True, slots=True)
class ActionResultSnapshot:
    """A portable action summary containing change counts instead of cell values."""

    action_id: str
    root_action_id: str
    cause: CausalRef | None
    kind: str
    name: str
    terminal: str
    timestamp: datetime
    sequence: CommitSequence | None
    reason: str | None
    changes: ChangeReport
    tags: frozenset[str] = frozenset()
    actor: ActorRef | None = None
    metadata: tuple[tuple[str, str], ...] = ()
    relation: ActionRelation | None = None
    conflict: ConflictDetail | None = None
    exception: ExceptionReport | None = None

    @classmethod
    def from_result(
        cls,
        result: ActionResult,
        policy: RedactionPolicy | None = None,
    ) -> ActionResultSnapshot:
        policy = DEFAULT_REDACTION if policy is None else policy
        context = result.context
        if isinstance(result, ActionCommit):
            terminal = "committed"
            timestamp = result.committed_at
            sequence = result.sequence
            reason = None
            changes = ChangeReport(len(result.patches), len(result.participant_changes))
            tags = result.tags
            conflict = None
            exception = None
        else:
            terminal = "rolled_back"
            timestamp = result.rolled_back_at
            sequence = None
            reason = result.reason.value
            changes = result.staged_report
            tags = frozenset()
            conflict = result.conflict
            exception = result.exception
        snapshot = cls(
            action_id=str(context.action_id),
            root_action_id=str(context.root_action_id),
            cause=context.cause,
            kind=context.kind.value,
            name=context.name,
            terminal=terminal,
            timestamp=timestamp,
            sequence=sequence,
            reason=reason,
            changes=changes,
            tags=tags,
            actor=context.actor,
            metadata=tuple(sorted(context.metadata.items())),
            relation=context.relation,
            conflict=conflict,
            exception=exception,
        )
        return policy.redact(snapshot)


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Explicit portable-result policy for metadata and exception messages."""

    include_actor: bool = True
    include_metadata: bool = False
    include_exception_messages: bool = False

    def redact[SnapshotT: CausalEventSnapshot](self, snapshot: SnapshotT) -> SnapshotT:
        """Return the same event variant with fields restricted by this policy."""
        if isinstance(snapshot, ActionResultSnapshot):
            return replace(
                snapshot,
                actor=snapshot.actor if self.include_actor else None,
                metadata=snapshot.metadata if self.include_metadata else (),
                exception=self.redact_exception(snapshot.exception),
            )
        exception = self.redact_exception(snapshot.exception)
        if isinstance(snapshot, ContinuationFailureSnapshot):
            assert exception is not None
            return replace(snapshot, exception=exception)
        return replace(snapshot, exception=exception)

    def redact_exception(self, report: ExceptionReport | None) -> ExceptionReport | None:
        if report is None or self.include_exception_messages:
            return report
        return ExceptionReport(report.type_name, "[redacted]")


DEFAULT_REDACTION = RedactionPolicy()


@dataclass(frozen=True, slots=True)
class OperationEventSnapshot:
    """One start or terminal transition of a causally identified operation execution."""

    execution_id: str
    root_action_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ResourceEventSnapshot:
    """One start or terminal transition of a causally identified resource generation."""

    generation_id: str
    root_action_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ContinuationFailureSnapshot:
    """A post-result failure that cannot alter the immutable action result."""

    failure_id: str
    root_action_id: str
    cause: CausalRef
    stage: str
    callback: str
    timestamp: datetime
    exception: ExceptionReport


type CausalEventSnapshot = (
    ActionResultSnapshot | OperationEventSnapshot | ResourceEventSnapshot | ContinuationFailureSnapshot
)

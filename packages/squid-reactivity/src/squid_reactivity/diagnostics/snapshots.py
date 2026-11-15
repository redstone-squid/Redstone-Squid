"""Portable diagnostic events and the policies that redact their sensitive fields."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from squid_reactivity.transactions.context import (
    ActorRef,
    CausalRef,
    ExceptionReport,
    TransactionRelation,
)
from squid_reactivity.transactions.results import (
    ChangeReport,
    CommitSequence,
    ConflictDetail,
    TransactionCommit,
    TransactionResult,
)


@dataclass(frozen=True, slots=True)
class TransactionResultSnapshot:
    """A transaction summary containing change counts instead of cell values."""

    transaction_id: str
    root_transaction_id: str
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
    relation: TransactionRelation | None = None
    conflict: ConflictDetail | None = None
    exception: ExceptionReport | None = None

    @classmethod
    def from_result(
        cls,
        result: TransactionResult,
        policy: RedactionPolicy | None = None,
    ) -> TransactionResultSnapshot:
        policy = DEFAULT_REDACTION if policy is None else policy
        context = result.context
        if isinstance(result, TransactionCommit):
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
            transaction_id=str(context.transaction_id),
            root_transaction_id=str(context.root_transaction_id),
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
class PreconditionSnapshot:
    """One effective version condition, without retaining its cell or value.

    Origin identifies the winning rule: explicit conditions override strong reads,
    which override ordinary reads of cells subsequently written.
    """

    target_id: str
    expected_version: int
    actual_version: int
    origin: Literal["read_write", "strong_read", "explicit"]


@dataclass(frozen=True, slots=True)
class TransactionValidationSnapshot:
    """Effective conditions sampled at validation, delivered after the outcome.

    An empty tuple means no cell version conditions applied. A body failure before
    validation produces no event. Participant-specific validation is not included.
    """

    transaction_id: str
    root_transaction_id: str
    cause: CausalRef | None
    timestamp: datetime
    preconditions: tuple[PreconditionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class RedactionPolicy:
    """Explicit portable-result policy for metadata and exception messages."""

    include_actor: bool = True
    include_metadata: bool = False
    include_exception_messages: bool = False

    def redact[SnapshotT: CausalEventSnapshot](self, snapshot: SnapshotT) -> SnapshotT:
        """Return the same event variant with fields restricted by this policy."""
        if isinstance(snapshot, TransactionValidationSnapshot):
            return snapshot
        if isinstance(snapshot, TransactionResultSnapshot):
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
    root_transaction_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ResourceEventSnapshot:
    """One start or terminal transition of a causally identified resource generation."""

    generation_id: str
    root_transaction_id: str | None
    cause: CausalRef | None
    name: str
    status: str
    timestamp: datetime
    exception: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class ContinuationFailureSnapshot:
    """A post-result failure that cannot alter the immutable transaction result."""

    failure_id: str
    root_transaction_id: str
    cause: CausalRef
    stage: str
    callback: str
    timestamp: datetime
    exception: ExceptionReport


type CausalEventSnapshot = (
    TransactionResultSnapshot
    | TransactionValidationSnapshot
    | OperationEventSnapshot
    | ResourceEventSnapshot
    | ContinuationFailureSnapshot
)

"""Describe committed changes and rolled-back work.

Commit records retain reversible cell patches and participant changes for history.
Rollback records describe discarded work. Diagnostic snapshots project either
result into summaries that do not retain live reactive owners.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any, Protocol

from squid_reactivity.transactions.context import ExceptionReport, TransactionContext

if TYPE_CHECKING:
    # Transaction execution constructs results; patches are only needed for annotations here.
    from squid_reactivity.transactions.patches import CellPatchSet


class RollbackReason(Enum):
    """Why a transaction discarded its staged writes."""

    HANDLER_EXCEPTION = "handler_exception"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    VALIDATION_FAILED = "validation_failed"
    PARTICIPANT_PREPARE_FAILED = "participant_prepare_failed"
    FRAMEWORK_INTEGRITY_FAILURE = "framework_integrity_failure"


@dataclass(frozen=True, slots=True)
class CommitSequence:
    """A diagnostic order within one thread, never comparable across thread runtime IDs."""

    runtime_id: uuid.UUID
    value: int


@dataclass(frozen=True, slots=True)
class ChangeReport:
    """A safe count-only projection of staged or committed changes."""

    cells: int = 0
    participants: int = 0


@dataclass(frozen=True, slots=True)
class ConflictDetail:
    """A target identifier and the versions that caused a conflict."""

    target_id: str
    expected_version: int
    actual_version: int


class ChangeToken[InverseT = Any](Protocol):
    """A participant's reversible change.

    The participant defines the inverse type. History first asks plan_inverse
    whether the change can still be undone, then passes the returned inverse
    to stage_inverse inside a transaction.
    """

    def plan_inverse(self) -> InverseT | ConflictDetail: ...

    def stage_inverse(self, inverse: InverseT) -> None: ...


@dataclass(frozen=True, slots=True)
class TransactionContribution:
    """An identified participant contribution with a token for undo."""

    participant_id: str
    token: ChangeToken[Any]


@dataclass(frozen=True, slots=True)
class TransactionCommit:
    """The immutable in-process record of a transaction that crossed the commit point."""

    context: TransactionContext
    sequence: CommitSequence
    committed_at: datetime
    duration: timedelta
    patches: CellPatchSet
    participant_changes: tuple[TransactionContribution, ...] = ()
    tags: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class TransactionRollback:
    """The result of a transaction that discarded its staged writes."""

    context: TransactionContext
    rolled_back_at: datetime
    duration: timedelta
    reason: RollbackReason
    conflict: ConflictDetail | None
    exception: ExceptionReport | None
    staged_report: ChangeReport
    cleanup_errors: tuple[ExceptionReport, ...] = ()


type TransactionResult = TransactionCommit | TransactionRollback

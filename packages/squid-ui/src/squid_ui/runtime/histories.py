"""Undo and redo committed UI actions, including explicit undo work outside the UI.

History records the state changes made by a successful action. Undo applies their
inverse changes in a new action; it does not rerun the original handler. By default,
version checks reject an undo that would overwrite a later change to the same state.

An external effect, such as a remote API write, cannot be reversed by restoring UI
state alone. Applications can supply a CompensationSpec for that work. Compensation
runs first, then history attempts the state undo and records whether the two agreed.
"""

import asyncio
import json
import logging
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal, Protocol, Self, overload

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from squid_reactivity import (
    FrameworkIntegrityError,
    FreshTransactionError,
    ReactiveConflictError,
    ReactiveWriteError,
    Signal,
)
from squid_reactivity.diagnostics import (
    OperationEventSnapshot,
    emit_causal_event,
    has_event_observers,
)
from squid_reactivity.operations import OperationContext
from squid_reactivity.transactions import (
    CausalRef,
    CellPatchSet,
    ChangeToken,
    ConditionalCellPatch,
    ConflictDetail,
    ExceptionReport,
    Transaction,
    TransactionCommit,
    TransactionContext,
    TransactionContribution,
    TransactionId,
    TransactionParticipant,
    TransactionPurpose,
    TransactionRelation,
    TransactionView,
    apply_conditional_patches,
    current_transaction,
    enlist,
    fresh_transaction,
)
from squid_ui.authoring.semantic import ControlGroup
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.text import TextLike, source_text
from squid_ui.errors import SquidUiError
from squid_ui.factories import action_control, control_group
from squid_ui.interactions import ActionEvent

logger = logging.getLogger(__name__)


class ReversibleOperation(Protocol):
    """A committed external change that can reverse itself.

    Implementations authorize and conditionally write in one domain transaction.
    Return the inverse of the actual committed reversal. Raise ReactiveConflictError
    only when no write occurred; other exceptions leave the outcome uncertain.
    """

    async def reverse(self) -> ReversibleOperation: ...


class HistoryError(SquidUiError, RuntimeError):
    """A history operation could not be honoured or was already reserved by this action."""


class HistoryEntryState(Enum):
    """Whether a recorded action can be undone, has been undone, or needs attention."""

    READY = "ready"
    REVERSING = "reversing"
    UNDONE = "undone"
    REAPPLYING = "reapplying"
    CONFLICTED = "conflicted"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class HistoryResultStatus(Enum):
    """The terminal result of one requested history operation."""

    APPLIED = "applied"
    EMPTY = "empty"
    CONFLICT = "conflict"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


@dataclass(frozen=True, slots=True)
class CompensationSpec:
    """Describe external undo work that can be retried with the same idempotency key.

    The application operation must make repeated calls safe. Retry limits count explicit
    undo attempts; the history does not run an automatic retry loop.
    """

    operation: Callable[[str], Awaitable[None]]
    idempotency_key: Callable[[TransactionCommit], str]
    retry: CompensationRetryPolicy = field(default_factory=lambda: CompensationRetryPolicy())


@dataclass(frozen=True, slots=True)
class CompensationRetryPolicy:
    """Explicit retry limit for separately requested compensation executions."""

    max_attempts: int | None = None

    def __post_init__(self) -> None:
        if self.max_attempts is not None and self.max_attempts < 1:
            message = "compensation max_attempts must be positive or None"
            raise ValueError(message)


class CompensationStatus(Enum):
    """Progress of external undo work and the state undo that follows it."""

    REVERTING = "reverting"
    EXTERNAL_SUCCEEDED = "external_succeeded"
    REVERTED = "reverted"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"
    NEEDS_RECONCILIATION = "needs_reconciliation"


@dataclass(slots=True)
class CompensationExecution:
    """One compensation attempt. Reaching a terminal status ends the execution."""

    context: OperationContext
    idempotency_key: str
    status: CompensationStatus = CompensationStatus.REVERTING
    error: Exception | None = None

    @property
    def execution_id(self) -> uuid.UUID:
        return self.context.execution_id

    def transition(self, status: CompensationStatus, error: Exception | None = None) -> None:
        """Record an inspectable transition without changing any transactional domain state."""
        self.status = status
        self.error = error
        if not has_event_observers():
            return
        report = None if error is None else ExceptionReport.capture(error)
        emit_causal_event(
            OperationEventSnapshot(
                str(self.context.execution_id),
                None if self.context.root_transaction_id is None else str(self.context.root_transaction_id),
                self.context.cause,
                self.context.name,
                status.value,
                datetime.now(UTC),
                report,
            )
        )


@dataclass(frozen=True, slots=True)
class CompensationIntent:
    """Record which transaction needs external undo work and the key used to deduplicate it."""

    context: OperationContext
    original_transaction_id: uuid.UUID
    idempotency_key: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class CompensationRecord:
    """The latest saved outcome and attempt count for external undo work."""

    intent: CompensationIntent
    status: CompensationStatus
    attempts: int
    updated_at: datetime
    error: ExceptionReport | None = None


@dataclass(frozen=True, slots=True)
class CompensationClaim:
    """An outbox decision to dispatch or skip an already completed external effect."""

    dispatch: bool
    attempts: int
    status: CompensationStatus


class CompensationOutbox(Protocol):
    """Application-owned persistence and deduplication for compensation intents."""

    async def claim(self, intent: CompensationIntent, retry: CompensationRetryPolicy) -> CompensationClaim: ...

    async def update(
        self,
        intent: CompensationIntent,
        status: CompensationStatus,
        error: ExceptionReport | None = None,
    ) -> None: ...


class TransactionalCompensationOutbox(CompensationOutbox, Protocol):
    """An outbox that saves an intent when the action requesting compensation commits.

    The participant joins that action's reactive transaction. If the transaction
    rolls back, neither its intent nor its action record is published, and history
    does not start the external undo work.
    """

    def participant(self, intent: CompensationIntent) -> TransactionParticipant[CompensationRecord | None]: ...


SETTLED_COMPENSATIONS = frozenset(
    {
        CompensationStatus.EXTERNAL_SUCCEEDED,
        CompensationStatus.REVERTED,
        CompensationStatus.NEEDS_RECONCILIATION,
    }
)
"""The statuses that answer "has this compensation already happened?" with yes.

A record in one of these is the outbox's entire reason to exist. Forgetting one turns the next
claim into a second execution of an external effect that already ran.
"""


class MemoryCompensationOutbox:
    """Bounded in-memory compensation records for tests or one-process use.

    Records deduplicate completed external effects while retained. Claims for unfinished
    records count as new attempts; this is not an exclusive worker claim. Updates move
    records to the newest position, and overflow evicts the least recently written one,
    including an in-flight record. dropped_records counts only evicted settled records.

    Restoring records can simulate a restart. Durable deduplication requires an
    application-owned outbox and an idempotent external operation.
    """

    def __init__(self, *, limit: int = 100, records: Iterable[CompensationRecord] = ()) -> None:
        if limit < 1:
            message = "a compensation outbox needs room for at least one record"
            raise ValueError(message)
        self.limit = limit
        self._records: OrderedDict[str, CompensationRecord] = OrderedDict()
        self._dropped = 0
        for record in records:
            self._put(record)

    @property
    def records(self) -> tuple[CompensationRecord, ...]:
        return tuple(self._records.values())

    @property
    def dropped_records(self) -> int:
        """How many settled answers the bound has discarded, each one a possible repeat."""
        return self._dropped

    async def claim(self, intent: CompensationIntent, retry: CompensationRetryPolicy) -> CompensationClaim:
        existing = self._records.get(intent.idempotency_key)
        if existing is not None and existing.status in SETTLED_COMPENSATIONS:
            return CompensationClaim(dispatch=False, attempts=existing.attempts, status=existing.status)
        attempts = 1 if existing is None else existing.attempts + 1
        if retry.max_attempts is not None and attempts > retry.max_attempts:
            return CompensationClaim(
                dispatch=False,
                attempts=existing.attempts if existing is not None else 0,
                status=CompensationStatus.FAILED,
            )
        self._put(CompensationRecord(intent, CompensationStatus.REVERTING, attempts, datetime.now(UTC)))
        return CompensationClaim(dispatch=True, attempts=attempts, status=CompensationStatus.REVERTING)

    async def update(
        self,
        intent: CompensationIntent,
        status: CompensationStatus,
        error: ExceptionReport | None = None,
    ) -> None:
        existing = self._records.get(intent.idempotency_key)
        # One is a floor, not a count, for a key the bound already forgot: the attempt being
        # reported is evidence of at least itself, and nothing here remembers the rest. The
        # status is what callers act on, and that is carried exactly.
        attempts = 1 if existing is None else existing.attempts
        self._put(CompensationRecord(intent, status, attempts, datetime.now(UTC), error))

    def participant(self, intent: CompensationIntent) -> _CompensationIntentParticipant:
        """Stage first-seen intent persistence in the same local commit as its action record."""
        return _CompensationIntentParticipant(self, intent)

    def _put(self, record: CompensationRecord) -> None:
        self._records.pop(record.intent.idempotency_key, None)
        self._records[record.intent.idempotency_key] = record
        while len(self._records) > self.limit:
            # Evict by last write, not status; neither settled nor in-flight records are pinned.
            key, evicted = self._records.popitem(last=False)
            if evicted.status in SETTLED_COMPENSATIONS:
                self._dropped += 1
                logger.warning(
                    "compensation outbox is full at %d records and forgot a settled one (%s, %s); "
                    "a later claim for that key will dispatch its external effect again",
                    self.limit,
                    key,
                    evicted.status.value,
                )


class _CompensationIntentParticipant:
    """Reference participant that installs a first compensation intent at the commit point."""

    def __init__(self, outbox: MemoryCompensationOutbox, intent: CompensationIntent) -> None:
        self.outbox = outbox
        self.intent = intent

    def prepare(self, view: TransactionView) -> CompensationRecord | None:
        if self.intent.idempotency_key in self.outbox._records:
            return None
        return CompensationRecord(self.intent, CompensationStatus.REVERTING, 0, datetime.now(UTC))

    def apply(self, prepared: CompensationRecord | None) -> None:
        if prepared is not None:
            self.outbox._put(prepared)

    def describe_change(self, prepared: CompensationRecord | None) -> None:
        return None

    def abort(self, prepared: CompensationRecord | None, cause: BaseException) -> None:
        return None

    def finalize(self, prepared: CompensationRecord | None) -> None:
        return None


class _CompensationRecordWire(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = Field(default=1, alias="schema")
    execution_id: uuid.UUID
    cause: CausalRef | None
    root_action_id: uuid.UUID | None
    name: str
    original_action_id: uuid.UUID
    idempotency_key: str
    started_at: datetime
    status: CompensationStatus
    attempts: int
    updated_at: datetime
    error: ExceptionReport | None


class CompensationRecordCodec:
    """JSON schema 1 codec for application-owned durable compensation records."""

    # Schema 1 retains its original JSON keys so saved records remain readable.
    schema_version = 1
    max_encoded_bytes = 65_536

    def encode(self, record: CompensationRecord) -> bytes:
        context = record.intent.context
        wire = _CompensationRecordWire(
            execution_id=context.execution_id,
            cause=context.cause,
            root_action_id=context.root_transaction_id,
            name=context.name,
            original_action_id=record.intent.original_transaction_id,
            idempotency_key=record.intent.idempotency_key,
            started_at=record.intent.started_at,
            status=record.status,
            attempts=record.attempts,
            updated_at=record.updated_at,
            error=record.error,
        )
        return json.dumps(wire.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")).encode()

    def decode(self, data: bytes) -> CompensationRecord:
        if len(data) > self.max_encoded_bytes:
            message = "compensation record exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            wire = _CompensationRecordWire.model_validate_json(data, strict=True)
        except ValidationError as error:
            try:
                payload = json.loads(data)
            except UnicodeDecodeError, json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict) and payload.get("schema") != self.schema_version:
                message = f"unsupported compensation record schema {payload.get('schema')!r}"
                raise ValueError(message) from error
            message = "compensation record has a corrupt schema"
            raise ValueError(message) from error
        context = OperationContext(
            wire.execution_id,
            wire.cause,
            wire.root_action_id,
            wire.name,
        )
        intent = CompensationIntent(
            context,
            wire.original_action_id,
            wire.idempotency_key,
            wire.started_at,
        )
        return CompensationRecord(
            intent,
            wire.status,
            wire.attempts,
            wire.updated_at,
            wire.error,
        )


@dataclass(frozen=True, slots=True)
class UndoPlan:
    """The state changes needed to reverse one committed action.

    Cell patches restore earlier values after checking their versions. Participant
    changes let transactional backends plan and validate their own inverses before
    history applies the combined undo.
    """

    cells: tuple[ConditionalCellPatch, ...]
    participants: tuple[TransactionContribution, ...] = ()

    @classmethod
    def from_commit(cls, commit: TransactionCommit) -> UndoPlan:
        patches: CellPatchSet = commit.patches
        return cls(patches.inverse(), commit.participant_changes)


@dataclass(slots=True)
class HistoryEntry:
    """One retained committed transaction and the fresh plan that can reverse it."""

    label: TextLike
    original_transaction_id: TransactionId
    undo_plan: UndoPlan
    state: HistoryEntryState = HistoryEntryState.READY
    recorded_at: float = field(default_factory=time.monotonic)
    last_result: HistoryResult | None = None
    undo_transaction_id: TransactionId | None = None
    redo_plan: UndoPlan | None = None
    compensation: CompensationSpec | None = None
    original_commit: TransactionCommit | None = None
    compensation_execution: CompensationExecution | None = None
    retentions: tuple[object, ...] = ()
    operation: ReversibleOperation | None = None
    targets: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """A typed undo or redo result; conflict always means no state changed."""

    status: HistoryResultStatus
    entry: HistoryEntry | None = None
    transaction_id: TransactionId | None = None
    conflict: ConflictDetail | None = None
    error: Exception | None = None

    @property
    def applied(self) -> bool:
        return self.status is HistoryResultStatus.APPLIED


type UndoResult = HistoryResult
type RedoResult = HistoryResult


@dataclass(frozen=True, slots=True)
class HistoryEntrySnapshot:
    """Safe diagnostic facts for one history entry."""

    label: str
    recorded_at: float
    transaction_id: str
    state: str
    conflict: str | None


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """A read-only view of one history stack."""

    name: str
    limit: int
    undo: tuple[HistoryEntrySnapshot, ...]
    redo: tuple[HistoryEntrySnapshot, ...]


class History:
    """Keep recent actions available for undo and redo.

    Call record during an action handler to retain that action's committed changes.
    Failed actions add nothing. Undo reverses the recorded changes, and redo reverses
    the changes that undo actually made. Both use new transactions, so the default
    version checks detect later edits rather than silently replacing them.

    Recording a new action clears redo and evicts the oldest undo entries beyond
    limit. Eviction also releases any backend resources retained for those entries.

    For actions with a CompensationSpec, external undo work runs before state undo.
    If that work succeeds but state or outbox recording fails, the result is
    NEEDS_RECONCILIATION: the application must resolve the disagreement. Compensated
    actions do not become redoable.
    """

    def __init__(
        self,
        *,
        limit: int = 20,
        compensation_outbox: CompensationOutbox | None = None,
    ) -> None:
        if limit < 1:
            message = "a history needs room for at least one entry"
            raise ValueError(message)
        self._signal = Signal()
        self._operation_lock = asyncio.Lock()
        self.limit = limit
        self._undone: list[HistoryEntry] = []
        self._redoable: list[HistoryEntry] = []
        self._compensation_outbox = compensation_outbox or MemoryCompensationOutbox(limit=limit)

    @property
    def can_undo(self) -> bool:
        self._signal.track()
        return bool(self._undone)

    @property
    def can_redo(self) -> bool:
        self._signal.track()
        return bool(self._redoable)

    @property
    def undo_label(self) -> TextLike | None:
        self._signal.track()
        return self._undone[-1].label if self._undone else None

    @property
    def redo_label(self) -> TextLike | None:
        self._signal.track()
        return self._redoable[-1].label if self._redoable else None

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        self._signal.track()
        return tuple(self._undone)

    @property
    def redoable(self) -> tuple[HistoryEntry, ...]:
        self._signal.track()
        return tuple(self._redoable)

    def snapshot(self, name: str) -> HistorySnapshot:
        self._signal.track()
        return HistorySnapshot(
            name,
            self.limit,
            tuple(_entry_snapshot(entry) for entry in self._undone),
            tuple(_entry_snapshot(entry) for entry in self._redoable),
        )

    def record(
        self,
        transaction: Transaction,
        label: TextLike,
        *,
        compensate: CompensationSpec | None = None,
    ) -> None:
        """Reserve this history's entry for the supplied active transaction.

        The hook captures the whole successful action, including changes made before this
        call. Only one operation may reserve this history in an action. Undo and redo
        commits do not create another normal history entry.
        """

        def committed(commit: TransactionCommit) -> None:
            if commit.context.kind in {TransactionPurpose.UNDO, TransactionPurpose.REDO}:
                return
            self._push(
                HistoryEntry(
                    label,
                    commit.context.transaction_id,
                    UndoPlan.from_commit(commit),
                    compensation=compensate,
                    original_commit=commit,
                )
            )

        self._reserve(transaction, committed)

    def record_operation(self, transaction: Transaction, label: TextLike, operation: ReversibleOperation) -> None:
        """Record a saved change without capturing incidental UI state.

        Pass the active transaction that successfully saved the change. The operation owns
        authorization, concurrency checks, and its inverse receipt.
        """
        self._reserve(
            transaction,
            lambda commit: self._push(
                HistoryEntry(label, commit.context.transaction_id, UndoPlan(()), operation=operation)
            ),
        )

    def accept_committed(
        self,
        label: TextLike,
        operation: ReversibleOperation,
        *,
        operation_id: TransactionId,
        targets: frozenset[str] = frozenset(),
    ) -> HistoryEntry:
        """Retain a confirmed external commit independently of the UI transaction.

        Use only after the application has established that the external write
        committed. A subsequent rendering error or UI rollback must not forget it.
        Repeated delivery returns the existing entry while it remains retained.
        """
        for entry in (*self._undone, *self._redoable):
            if entry.original_transaction_id == operation_id:
                return entry
        entry = HistoryEntry(label, operation_id, UndoPlan(()), operation=operation, targets=targets)
        self._push(entry)
        return entry

    def settle_reversal(self, transaction_id: TransactionId, *, inverse: ReversibleOperation | None) -> None:
        """Resolve an uncertain external reversal after the application checks its outcome.

        Pass the committed inverse to finish moving the entry, or None after
        proving no write occurred. No external operation is executed here.
        """
        entry = self._select(self._undone, transaction_id)
        undoing = entry is not None
        if entry is None:
            entry = self._select(self._redoable, transaction_id)
        if entry is None or entry.state is not HistoryEntryState.NEEDS_RECONCILIATION:
            message = "only an uncertain retained reversal can be reconciled"
            raise HistoryError(message)
        if inverse is not None:
            entry.operation = inverse
            source, destination = (self._undone, self._redoable) if undoing else (self._redoable, self._undone)
            self._remove_identity(source, entry)
            destination.append(entry)
            entry.state = HistoryEntryState.UNDONE if undoing else HistoryEntryState.READY
            entry.last_result = HistoryResult(HistoryResultStatus.APPLIED, entry)
        else:
            entry.state = HistoryEntryState.READY if undoing else HistoryEntryState.UNDONE
            entry.last_result = None
        self._signal.notify()

    def discard_targets(self, targets: frozenset[str]) -> None:
        """Forget receipts touching these targets, preserving unrelated edits."""
        selected = [entry for entry in (*self._undone, *self._redoable) if entry.targets & targets]
        if any(entry.state in {HistoryEntryState.REVERSING, HistoryEntryState.REAPPLYING} for entry in selected):
            message = "cannot discard an operation while it is running"
            raise HistoryError(message)
        for entry in selected:
            self._remove_identity(self._undone, entry)
            self._remove_identity(self._redoable, entry)
            self._release_entry(entry)
        if selected:
            self._signal.notify()

    async def _reverse_operation(self, entry: HistoryEntry, *, redo: bool) -> HistoryResult:
        assert entry.operation is not None
        if entry.state is HistoryEntryState.NEEDS_RECONCILIATION:
            assert entry.last_result is not None
            return entry.last_result
        entry.state = HistoryEntryState.REAPPLYING if redo else HistoryEntryState.REVERSING
        active = current_transaction()
        cause = None if active is None else active.context
        context = TransactionContext.create(
            f"{'Redo' if redo else 'Undo'} {source_text(entry.label)}",
            kind=TransactionPurpose.REDO if redo else TransactionPurpose.UNDO,
            cause=None if cause is None else cause.causal_ref(),
            relation=TransactionRelation("reapplies" if redo else "reverses", entry.original_transaction_id),
        )
        try:
            with fresh_transaction(transaction_context=context):
                inverse = await entry.operation.reverse()
        except FreshTransactionError:
            entry.state = HistoryEntryState.UNDONE if redo else HistoryEntryState.READY
            raise
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, conflict=error.detail, error=error)
        except BaseException as error:
            # An external commit may have succeeded before transport failure or cancellation.
            # Retain the receipt but forbid automatic retries until reconciled by the caller.
            entry.state = HistoryEntryState.NEEDS_RECONCILIATION
            entry.last_result = HistoryResult(
                HistoryResultStatus.NEEDS_RECONCILIATION, entry, error=error if isinstance(error, Exception) else None
            )
            self._signal.notify()
            if not isinstance(error, Exception):
                raise
            return entry.last_result
        else:
            entry.operation = inverse
            source, destination = (self._redoable, self._undone) if redo else (self._undone, self._redoable)
            self._remove_identity(source, entry)
            destination.append(entry)
            entry.state = HistoryEntryState.READY if redo else HistoryEntryState.UNDONE
            result = HistoryResult(HistoryResultStatus.APPLIED, entry)
        entry.last_result = result
        self._signal.notify()
        return result

    async def undo(self, transaction_id: TransactionId | None = None) -> UndoResult:
        """Serialize reversals, including asynchronous external operations."""
        async with self._operation_lock:
            return await self._undo(transaction_id)

    async def _undo(self, transaction_id: TransactionId | None = None) -> UndoResult:
        """Reverse the latest retained action, or the one with the supplied original action ID.

        Return EMPTY when absent. Conflicts leave transactional state unchanged; compensation
        may instead return NEEDS_RECONCILIATION after an external effect has succeeded.
        """
        entry = self._select(self._undone, transaction_id)
        if entry is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        if entry.state is HistoryEntryState.NEEDS_RECONCILIATION and entry.last_result is not None:
            return entry.last_result
        if entry.operation is not None:
            return await self._reverse_operation(entry, redo=False)
        if entry.compensation is not None:
            return await self._compensate(entry)
        return self._undo_state(entry)

    def _undo_state(
        self,
        entry: HistoryEntry,
        *,
        retain_redo: bool = True,
        transaction_context: TransactionContext | None = None,
    ) -> UndoResult:
        previous = entry.state
        entry.state = HistoryEntryState.REVERSING
        active = current_transaction()
        cause = None if active is None else active.context
        context = transaction_context or TransactionContext.create(
            f"Undo {source_text(entry.label)}",
            kind=TransactionPurpose.UNDO,
            cause=None if cause is None else cause.causal_ref(),
            root_transaction_id=None if cause is None else cause.root_transaction_id,
            relation=TransactionRelation("reverses", entry.original_transaction_id),
        )
        try:
            with fresh_transaction(transaction_context=context) as tx:
                planned: list[tuple[ChangeToken[Any], object]] = []
                for change in entry.undo_plan.participants:
                    inverse = change.token.plan_inverse()
                    if isinstance(inverse, ConflictDetail):
                        raise ReactiveConflictError(  # noqa: TRY301
                            inverse, f"{change.participant_id} cannot be inverted safely"
                        )
                    planned.append((change.token, inverse))
                apply_conditional_patches(entry.undo_plan.cells)
                for token, inverse in planned:
                    token.stage_inverse(inverse)

                def committed(commit: TransactionCommit) -> None:
                    assert entry is not None
                    self._release_entry(entry)
                    entry.undo_transaction_id = commit.context.transaction_id
                    entry.redo_plan = UndoPlan.from_commit(commit)
                    entry.state = HistoryEntryState.UNDONE
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.transaction_id)
                    entry.last_result = result
                    self._remove_identity(self._undone, entry)
                    if retain_redo:
                        entry.retentions = self._retain_plan(entry.redo_plan)
                        self._redoable.append(entry)
                    self._signal.notify()

                tx.after_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.transaction_id, error.detail, error)
            entry.last_result = result
            self._signal.notify()
            return result
        except FrameworkIntegrityError, FreshTransactionError:
            # No inverse was admitted, so the entry is still whatever it was selected as.
            entry.state = previous
            raise
        except Exception as error:
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, context.transaction_id, error=error)
            entry.last_result = result
            self._signal.notify()
            return result
        assert entry.last_result is not None
        return entry.last_result

    async def _compensate(self, entry: HistoryEntry) -> UndoResult:
        assert entry.compensation is not None and entry.original_commit is not None
        key = entry.compensation.idempotency_key(entry.original_commit)
        original = entry.original_commit.context
        operation_context = OperationContext(
            uuid.uuid7(),
            original.causal_ref(),
            original.root_transaction_id,
            f"Compensate {source_text(entry.label)}",
        )
        execution = CompensationExecution(operation_context, key)
        execution.transition(CompensationStatus.REVERTING)
        intent = CompensationIntent(operation_context, original.transaction_id, key, datetime.now(UTC))
        previous = entry.state
        entry.compensation_execution = execution
        entry.state = HistoryEntryState.REVERSING
        self._signal.notify()
        intent_context = TransactionContext.create(
            f"Begin compensation for {source_text(entry.label)}",
            kind=TransactionPurpose.COMPENSATION,
            cause=operation_context.causal_ref(),
            root_transaction_id=original.root_transaction_id,
            relation=TransactionRelation("compensates", original.transaction_id),
        )
        try:
            with fresh_transaction(transaction_context=intent_context):
                participant = getattr(self._compensation_outbox, "participant", None)
                if participant is not None:
                    joined = enlist(
                        (self._compensation_outbox, key),
                        lambda: participant(intent),
                    )
                    assert joined is not None
            claim = await self._compensation_outbox.claim(intent, entry.compensation.retry)
        except asyncio.CancelledError:
            execution.transition(CompensationStatus.CANCELLED)
            entry.state = HistoryEntryState.FAILED
            self._signal.notify()
            raise
        except FrameworkIntegrityError, FreshTransactionError:
            # No compensation was admitted, so the entry is still whatever it was selected as.
            entry.state = previous
            self._signal.notify()
            raise
        except Exception as error:
            execution.transition(CompensationStatus.FAILED, error)
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, error=error)
            entry.last_result = result
            self._signal.notify()
            return result
        if not claim.dispatch and claim.status is CompensationStatus.FAILED:
            error = RuntimeError("compensation retry limit exhausted")
            execution.transition(CompensationStatus.FAILED, error)
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, error=error)
            entry.last_result = result
            self._signal.notify()
            return result
        if claim.dispatch:
            try:
                await entry.compensation.operation(key)
            except asyncio.CancelledError as error:
                execution.transition(CompensationStatus.CANCELLED)
                entry.state = HistoryEntryState.FAILED
                await self._compensation_outbox.update(
                    intent, CompensationStatus.CANCELLED, ExceptionReport.capture(error)
                )
                self._signal.notify()
                raise
            except Exception as error:
                external_error = error
                try:
                    await self._compensation_outbox.update(
                        intent, CompensationStatus.FAILED, ExceptionReport.capture(external_error)
                    )
                except Exception as outbox_error:
                    error = ExceptionGroup(
                        "external compensation and outbox recording both failed",
                        (external_error, outbox_error),
                    )
                execution.transition(CompensationStatus.FAILED, error)
                entry.state = HistoryEntryState.FAILED
                result = HistoryResult(HistoryResultStatus.FAILED, entry, error=error)
                entry.last_result = result
                self._signal.notify()
                return result
            execution.transition(CompensationStatus.EXTERNAL_SUCCEEDED)
            try:
                await self._compensation_outbox.update(intent, CompensationStatus.EXTERNAL_SUCCEEDED)
            except Exception as error:
                execution.transition(CompensationStatus.NEEDS_RECONCILIATION, error)
                entry.state = HistoryEntryState.NEEDS_RECONCILIATION
                result = HistoryResult(HistoryResultStatus.NEEDS_RECONCILIATION, entry, error=error)
                entry.last_result = result
                self._signal.notify()
                return result
        compensation_context = TransactionContext.create(
            f"Apply compensation for {source_text(entry.label)}",
            kind=TransactionPurpose.COMPENSATION,
            cause=operation_context.causal_ref(),
            root_transaction_id=original.root_transaction_id,
            relation=TransactionRelation("compensates", original.transaction_id),
        )
        result = self._undo_state(entry, retain_redo=False, transaction_context=compensation_context)
        if result.status in {HistoryResultStatus.CONFLICT, HistoryResultStatus.FAILED}:
            execution.transition(CompensationStatus.NEEDS_RECONCILIATION)
            entry.state = HistoryEntryState.NEEDS_RECONCILIATION
            try:
                await self._compensation_outbox.update(intent, CompensationStatus.NEEDS_RECONCILIATION)
                outbox_error = None
            except Exception as error:
                outbox_error = error
            result = HistoryResult(
                HistoryResultStatus.NEEDS_RECONCILIATION,
                entry,
                result.transaction_id,
                result.conflict,
                outbox_error or result.error,
            )
            entry.last_result = result
        else:
            execution.transition(CompensationStatus.REVERTED)
            try:
                await self._compensation_outbox.update(intent, CompensationStatus.REVERTED)
            except Exception as error:
                execution.transition(CompensationStatus.NEEDS_RECONCILIATION, error)
                entry.state = HistoryEntryState.NEEDS_RECONCILIATION
                result = HistoryResult(
                    HistoryResultStatus.NEEDS_RECONCILIATION,
                    entry,
                    result.transaction_id,
                    error=error,
                )
                entry.last_result = result
                if not any(candidate is entry for candidate in self._undone):
                    self._undone.append(entry)
        self._signal.notify()
        return result

    async def redo(self) -> RedoResult:
        """Serialize redo with other reversals on this stack."""
        async with self._operation_lock:
            return await self._redo()

    async def _redo(self) -> RedoResult:
        """Restore the latest undone action by reversing the changes made by its undo.

        The redo plan uses versions captured during undo, so it checks changes made
        since then rather than comparing against the original action's old versions.
        """
        entry = self._select(self._redoable, None)
        if entry is not None and entry.operation is not None:
            return await self._reverse_operation(entry, redo=True)
        if entry is None or entry.redo_plan is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        previous = entry.state
        entry.state = HistoryEntryState.REAPPLYING
        active = current_transaction()
        cause = None if active is None else active.context
        context = TransactionContext.create(
            f"Redo {source_text(entry.label)}",
            kind=TransactionPurpose.REDO,
            cause=None if cause is None else cause.causal_ref(),
            root_transaction_id=None if cause is None else cause.root_transaction_id,
            relation=TransactionRelation("reapplies", entry.original_transaction_id),
        )
        try:
            with fresh_transaction(transaction_context=context) as tx:
                planned: list[tuple[ChangeToken[Any], object]] = []
                for change in entry.redo_plan.participants:
                    inverse = change.token.plan_inverse()
                    if isinstance(inverse, ConflictDetail):
                        raise ReactiveConflictError(  # noqa: TRY301
                            inverse, f"{change.participant_id} cannot be reapplied safely"
                        )
                    planned.append((change.token, inverse))
                apply_conditional_patches(entry.redo_plan.cells)
                for token, inverse in planned:
                    token.stage_inverse(inverse)

                def committed(commit: TransactionCommit) -> None:
                    assert entry is not None
                    self._release_entry(entry)
                    entry.undo_plan = UndoPlan.from_commit(commit)
                    entry.retentions = self._retain_plan(entry.undo_plan)
                    entry.state = HistoryEntryState.READY
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.transaction_id)
                    entry.last_result = result
                    self._remove_identity(self._redoable, entry)
                    self._undone.append(entry)
                    self._signal.notify()

                tx.after_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.transaction_id, error.detail, error)
            entry.last_result = result
            self._signal.notify()
            return result
        except FrameworkIntegrityError, FreshTransactionError:
            # No inverse was admitted, so the entry is still whatever it was selected as.
            entry.state = previous
            raise
        except Exception as error:
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, context.transaction_id, error=error)
            entry.last_result = result
            self._signal.notify()
            return result
        assert entry.last_result is not None
        return entry.last_result

    def delete_conflicted(self, transaction_id: TransactionId | None = None) -> HistoryEntry | None:
        """Remove a matching conflicted entry without changing application state.

        With no action ID, inspect only the newest entry of each stack; this does not search
        past an unconflicted top entry.
        """
        for stack in (self._undone, self._redoable):
            entry = self._select(stack, transaction_id)
            if entry is not None and entry.state is HistoryEntryState.CONFLICTED:
                self._remove_identity(stack, entry)
                self._release_entry(entry)
                self._signal.notify()
                return entry
        return None

    def clear(self) -> None:
        """Release retained inverse leases and forget both stacks without changing application state."""
        if not (self._undone or self._redoable):
            return
        for entry in (*self._undone, *self._redoable):
            self._release_entry(entry)
        self._undone.clear()
        self._redoable.clear()
        self._signal.notify()

    def _reserve(self, transaction: Transaction, callback: Callable[[TransactionCommit], None]) -> None:
        if transaction.readonly:
            message = "a read-only action changed nothing, so it has nothing to record"
            raise ReactiveWriteError(message)
        if transaction.has_hook(self):
            message = "this action already used this history; only one history operation is allowed"
            raise HistoryError(message)
        transaction.after_commit(callback, key=self)

    def _push(self, entry: HistoryEntry) -> None:
        entry.retentions = self._retain_plan(entry.undo_plan)
        try:
            retain = getattr(entry.operation, "retain", None)
            if retain is not None:
                entry.retentions += (retain(),)
        except BaseException:
            self._release_entry(entry)
            raise
        self._undone.append(entry)
        evicted = self._undone[: max(0, len(self._undone) - self.limit)]
        for candidate in evicted:
            self._release_entry(candidate)
        del self._undone[: len(evicted)]
        for candidate in self._redoable:
            self._release_entry(candidate)
        self._redoable.clear()
        self._signal.notify()

    @staticmethod
    def _retain_plan(plan: UndoPlan) -> tuple[object, ...]:
        leases: list[object] = []
        try:
            for change in plan.participants:
                retain = getattr(change.token, "retain", None)
                if retain is not None:
                    leases.append(retain())
        except BaseException:
            for lease in leases:
                release = getattr(lease, "release", None)
                if release is not None:
                    release()
            raise
        return tuple(leases)

    @staticmethod
    def _release_entry(entry: HistoryEntry) -> None:
        retentions, entry.retentions = entry.retentions, ()
        for lease in retentions:
            release = getattr(lease, "release", None)
            if release is not None:
                release()

    @staticmethod
    def _select(stack: list[HistoryEntry], transaction_id: TransactionId | None) -> HistoryEntry | None:
        if transaction_id is None:
            return stack[-1] if stack else None
        return next((entry for entry in reversed(stack) if entry.original_transaction_id == transaction_id), None)

    @staticmethod
    def _remove_identity(stack: list[HistoryEntry], entry: HistoryEntry) -> None:
        for index, candidate in enumerate(stack):
            if candidate is entry:
                del stack[index]
                return


class _HistoryField:
    def __init__(self, limit: int, compensation_outbox: CompensationOutbox | None) -> None:
        self._limit = limit
        self._compensation_outbox = compensation_outbox
        self._slot = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._slot = f"__history_{name}"

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> Self: ...

    @overload
    def __get__(self, instance: object, owner: type | None = None) -> History: ...

    def __get__(self, instance: object | None, owner: type | None = None) -> Self | History:
        """Return the descriptor on the class, or lazily create the instance's history."""
        if instance is None:
            return self
        # Cache outside declared state: history manages its own observation and transaction hooks.
        stack = instance.__dict__.get(self._slot)
        if stack is None:
            stack = History(limit=self._limit, compensation_outbox=self._compensation_outbox)
            instance.__dict__[self._slot] = stack
        return stack


def history(*, limit: int = 20, compensation_outbox: CompensationOutbox | None = None) -> History:
    """Declare a separate undo/redo stack for each instance.

    Assign this at class scope, for example ``edits: History = history(limit=20)``.
    The stack is created on first access. Clear it when its retained backend undo
    resources are no longer needed.
    """
    return _HistoryField(limit, compensation_outbox)  # type: ignore[bad-return]


def history_actions(stack: History, *, key: str = "history", chrome: Chrome = DEFAULT_CHROME) -> ControlGroup:
    """Build ordinary controls for the history's current LIFO entries."""

    async def undo(event: ActionEvent) -> None:
        await stack.undo()

    async def redo(event: ActionEvent) -> None:
        await stack.redo()

    return control_group(
        action_control(chrome.undo, undo, key=f"{key}.undo", available=stack.can_undo),
        action_control(chrome.redo, redo, key=f"{key}.redo", available=stack.can_redo),
        key=key,
    )


def inspect_histories(owner: object) -> tuple[HistorySnapshot, ...]:
    """Snapshot declared histories, creating their per-instance stacks on first inspection."""
    snapshots: list[HistorySnapshot] = []
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _HistoryField):
                snapshots.append(getattr(owner, name).snapshot(name))
    return tuple(snapshots)


def _entry_snapshot(entry: HistoryEntry) -> HistoryEntrySnapshot:
    conflict = (
        None if entry.last_result is None or entry.last_result.conflict is None else str(entry.last_result.conflict)
    )
    return HistoryEntrySnapshot(
        source_text(entry.label), entry.recorded_at, str(entry.original_transaction_id), entry.state.value, conflict
    )


__all__ = [
    "CompensationClaim",
    "CompensationExecution",
    "CompensationIntent",
    "CompensationOutbox",
    "CompensationRecord",
    "CompensationRecordCodec",
    "CompensationRetryPolicy",
    "CompensationSpec",
    "CompensationStatus",
    "History",
    "HistoryEntry",
    "HistoryEntrySnapshot",
    "HistoryEntryState",
    "HistoryError",
    "HistoryResult",
    "HistoryResultStatus",
    "HistorySnapshot",
    "MemoryCompensationOutbox",
    "RedoResult",
    "ReversibleOperation",
    "TransactionalCompensationOutbox",
    "UndoPlan",
    "UndoResult",
    "history",
    "history_actions",
    "inspect_histories",
]

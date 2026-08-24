"""Component-owned history derived from immutable committed action lineage."""

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.factories import action, action_group
from squid_layouts.interactions import ActionEvent
from squid_layouts.runtime.reactivity import (
    ActionCommit,
    ActionContext,
    ActionKind,
    ActionParticipant,
    CellPatchSet,
    ConditionalCellPatch,
    FrameworkIntegrityError,
    FreshActionError,
    ReactiveConflictError,
    TransactionView,
    apply_conditional_patches,
    apply_local_overwrite_patches,
    fresh_action_transaction,
    has_action_hook,
    join_action,
    on_action_commit,
)
from squid_layouts.semantic import ActionGroup
from squid_layouts.text import TextLike
from squid_reactive.actions import (
    DEFAULT_REDACTION,
    CausalRef,
    ConflictDetail,
    ExceptionSummary,
    OperationEventSnapshot,
    ParticipantChange,
    current_action,
    emit_causal_event,
)
from squid_reactive.operations import OperationContext


class HistoryError(RuntimeError):
    """A history operation could not be honoured or was already reserved by this action."""


class HistoryEntryState(Enum):
    """The inspectable lifecycle of a retained inverse plan."""

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


class UndoStrategy(Enum):
    """How retained register patches may be applied."""

    CONDITIONAL = "conditional"
    LOCAL_OVERWRITE = "local_overwrite"


class HistoryOwner(Protocol):
    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class CompensationSpec:
    """A repeatable external compensator with a stable idempotency key."""

    operation: Callable[[str], Awaitable[None]]
    idempotency_key: Callable[[ActionCommit], str]
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
    """Truthful terminal and in-flight saga states."""

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
        summary = None if error is None else DEFAULT_REDACTION.exception(ExceptionSummary.capture(error))
        emit_causal_event(
            OperationEventSnapshot(
                str(self.context.execution_id),
                None if self.context.root_action_id is None else str(self.context.root_action_id),
                self.context.cause,
                self.context.name,
                status.value,
                datetime.now(UTC),
                summary,
            )
        )


@dataclass(frozen=True, slots=True)
class CompensationIntent:
    """Portable intent for one causally identified compensation attempt."""

    context: OperationContext
    original_action_id: uuid.UUID
    idempotency_key: str
    started_at: datetime


@dataclass(frozen=True, slots=True)
class CompensationRecord:
    """Portable latest state of one idempotent compensation saga."""

    intent: CompensationIntent
    status: CompensationStatus
    attempts: int
    updated_at: datetime
    error: ExceptionSummary | None = None


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
        error: ExceptionSummary | None = None,
    ) -> None: ...


class TransactionalCompensationOutbox(CompensationOutbox, Protocol):
    """An outbox that can atomically retain first intent through the Squid commit gate."""

    def participant(self, intent: CompensationIntent) -> ActionParticipant[CompensationRecord | None]: ...


class MemoryCompensationOutbox:
    """Bounded reference outbox; ``records=`` rebuilds one after a simulated restart."""

    def __init__(self, *, limit: int = 100, records: Iterable[CompensationRecord] = ()) -> None:
        if limit < 1:
            message = "a compensation outbox needs room for at least one record"
            raise ValueError(message)
        self.limit = limit
        self._records: OrderedDict[str, CompensationRecord] = OrderedDict()
        for record in records:
            self._put(record)

    @property
    def records(self) -> tuple[CompensationRecord, ...]:
        return tuple(self._records.values())

    async def claim(self, intent: CompensationIntent, retry: CompensationRetryPolicy) -> CompensationClaim:
        existing = self._records.get(intent.idempotency_key)
        if existing is not None and existing.status in {
            CompensationStatus.EXTERNAL_SUCCEEDED,
            CompensationStatus.REVERTED,
            CompensationStatus.NEEDS_RECONCILIATION,
        }:
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
        error: ExceptionSummary | None = None,
    ) -> None:
        existing = self._records.get(intent.idempotency_key)
        attempts = 1 if existing is None else existing.attempts
        self._put(CompensationRecord(intent, status, attempts, datetime.now(UTC), error))

    def participant(self, intent: CompensationIntent) -> _CompensationIntentParticipant:
        """Stage first-seen intent persistence in the same local commit as its action record."""
        return _CompensationIntentParticipant(self, intent)

    def _put(self, record: CompensationRecord) -> None:
        self._records.pop(record.intent.idempotency_key, None)
        self._records[record.intent.idempotency_key] = record
        while len(self._records) > self.limit:
            self._records.popitem(last=False)


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


class CompensationRecordCodec:
    """JSON schema 1 codec for application-owned durable compensation records."""

    schema_version = 1
    max_encoded_bytes = 65_536

    def encode(self, record: CompensationRecord) -> bytes:
        context = record.intent.context
        payload = {
            "schema": self.schema_version,
            "execution_id": str(context.execution_id),
            "cause": None
            if context.cause is None
            else {"kind": context.cause.kind, "identity": context.cause.identity},
            "root_action_id": None if context.root_action_id is None else str(context.root_action_id),
            "name": context.name,
            "original_action_id": str(record.intent.original_action_id),
            "idempotency_key": record.intent.idempotency_key,
            "started_at": record.intent.started_at.isoformat(),
            "status": record.status.value,
            "attempts": record.attempts,
            "updated_at": record.updated_at.isoformat(),
            "error": None
            if record.error is None
            else {"type_name": record.error.type_name, "message": record.error.message},
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def decode(self, data: bytes) -> CompensationRecord:
        if len(data) > self.max_encoded_bytes:
            message = "compensation record exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            return self._decode(data)
        except (KeyError, TypeError, AttributeError, UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "compensation record has a corrupt schema"
            raise ValueError(message) from error
        except ValueError as error:
            if str(error).startswith("unsupported compensation record schema"):
                raise
            message = "compensation record has a corrupt schema"
            raise ValueError(message) from error

    def _decode(self, data: bytes) -> CompensationRecord:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            message = "compensation record has a corrupt schema"
            raise TypeError(message)
        if payload.get("schema") != self.schema_version:
            message = f"unsupported compensation record schema {payload.get('schema')!r}"
            raise ValueError(message)
        cause = payload["cause"]
        context = OperationContext(
            uuid.UUID(payload["execution_id"]),
            None if cause is None else CausalRef(cause["kind"], cause["identity"]),
            None if payload["root_action_id"] is None else uuid.UUID(payload["root_action_id"]),
            payload["name"],
        )
        intent = CompensationIntent(
            context,
            uuid.UUID(payload["original_action_id"]),
            payload["idempotency_key"],
            datetime.fromisoformat(payload["started_at"]),
        )
        error = payload["error"]
        return CompensationRecord(
            intent,
            CompensationStatus(payload["status"]),
            payload["attempts"],
            datetime.fromisoformat(payload["updated_at"]),
            None if error is None else ExceptionSummary(error["type_name"], error["message"]),
        )


@dataclass(frozen=True, slots=True)
class UndoPlan:
    """An atomic set of version-conditional physical inverses."""

    cells: tuple[ConditionalCellPatch, ...]
    participants: tuple[ParticipantChange, ...] = ()

    @classmethod
    def from_commit(cls, commit: ActionCommit) -> UndoPlan:
        patches: CellPatchSet = commit.patches
        return cls(patches.inverse(), commit.participant_changes)


@dataclass(slots=True)
class HistoryEntry:
    """One retained committed action and the fresh plan that can reverse it."""

    label: TextLike
    original_action_id: object
    undo_plan: UndoPlan
    state: HistoryEntryState = HistoryEntryState.READY
    recorded_at: float = field(default_factory=time.monotonic)
    last_result: HistoryResult | None = None
    undo_action_id: object | None = None
    redo_plan: UndoPlan | None = None
    compensation: CompensationSpec | None = None
    original_commit: ActionCommit | None = None
    compensation_execution: CompensationExecution | None = None
    strategy: UndoStrategy = UndoStrategy.CONDITIONAL


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """A typed undo or redo result; conflict always means no state changed."""

    status: HistoryResultStatus
    entry: HistoryEntry | None = None
    action_id: object | None = None
    conflict: object | None = None
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
    action_id: str
    state: str
    conflict: str | None


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """A read-only view of one component-owned history stack."""

    name: str
    limit: int
    undo: tuple[HistoryEntrySnapshot, ...]
    redo: tuple[HistoryEntrySnapshot, ...]


class History:
    """One component's bounded commit-derived undo stack."""

    def __init__(
        self,
        owner: HistoryOwner,
        *,
        limit: int = 20,
        compensation_outbox: CompensationOutbox | None = None,
    ) -> None:
        if limit < 1:
            message = "a history needs room for at least one entry"
            raise ValueError(message)
        self._owner = owner
        self.limit = limit
        self._undone: list[HistoryEntry] = []
        self._redoable: list[HistoryEntry] = []
        self._compensation_outbox = compensation_outbox or MemoryCompensationOutbox(limit=limit)

    @property
    def can_undo(self) -> bool:
        return bool(self._undone)

    @property
    def can_redo(self) -> bool:
        return bool(self._redoable)

    @property
    def undo_label(self) -> TextLike | None:
        return self._undone[-1].label if self._undone else None

    @property
    def redo_label(self) -> TextLike | None:
        return self._redoable[-1].label if self._redoable else None

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._undone)

    @property
    def redoable(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._redoable)

    def snapshot(self, name: str) -> HistorySnapshot:
        return HistorySnapshot(
            name,
            self.limit,
            tuple(_entry_snapshot(entry) for entry in self._undone),
            tuple(_entry_snapshot(entry) for entry in self._redoable),
        )

    def record(
        self,
        label: TextLike,
        *,
        compensate: CompensationSpec | None = None,
        strategy: UndoStrategy = UndoStrategy.CONDITIONAL,
    ) -> None:
        """Retain the whole successful action once, using its committed patch lineage."""

        def committed(commit: ActionCommit, aftermath: object) -> None:
            if commit.context.kind in {ActionKind.UNDO, ActionKind.REDO}:
                return
            self._push(
                HistoryEntry(
                    label,
                    commit.context.action_id,
                    UndoPlan.from_commit(commit),
                    compensation=compensate,
                    original_commit=commit,
                    strategy=strategy,
                )
            )

        self._reserve(committed)

    async def undo(self, action_id: object | None = None) -> UndoResult:
        """Conditionally reverse one retained action as a fresh ``UNDO`` action."""
        entry = self._select(self._undone, action_id)
        if entry is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        if entry.state is HistoryEntryState.NEEDS_RECONCILIATION and entry.last_result is not None:
            return entry.last_result
        if entry.compensation is not None:
            return await self._compensate(entry)
        return self._undo_state(entry)

    def _undo_state(
        self,
        entry: HistoryEntry,
        *,
        retain_redo: bool = True,
        action_context: ActionContext | None = None,
    ) -> UndoResult:
        entry.state = HistoryEntryState.REVERSING
        cause = current_action()
        context = action_context or ActionContext.create(
            f"Undo {entry.label}",
            kind=ActionKind.UNDO,
            cause=None if cause is None else cause.causal_ref(),
            root_action_id=None if cause is None else cause.root_action_id,
            reverses_action_id=entry.original_action_id,
        )
        try:
            with fresh_action_transaction(action_context=context):
                planned: list[tuple[object, object]] = []
                if entry.strategy is UndoStrategy.LOCAL_OVERWRITE and entry.undo_plan.participants:
                    detail = ConflictDetail("participant", 0, 0)
                    raise ReactiveConflictError(  # noqa: TRY301
                        detail, "local overwrite policy cannot target transaction participants"
                    )
                for change in entry.undo_plan.participants:
                    inverse = change.token.plan_inverse()
                    if isinstance(inverse, ConflictDetail):
                        raise ReactiveConflictError(  # noqa: TRY301
                            inverse, f"{change.participant_id} cannot be inverted safely"
                        )
                    planned.append((change.token, inverse))
                if entry.strategy is UndoStrategy.LOCAL_OVERWRITE:
                    apply_local_overwrite_patches(entry.undo_plan.cells)
                else:
                    apply_conditional_patches(entry.undo_plan.cells)
                for token, inverse in planned:
                    token.stage_inverse(inverse)

                def committed(commit: ActionCommit, aftermath: object) -> None:
                    entry.undo_action_id = commit.context.action_id
                    entry.redo_plan = UndoPlan.from_commit(commit)
                    entry.state = HistoryEntryState.UNDONE
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.action_id)
                    entry.last_result = result
                    self._remove_identity(self._undone, entry)
                    if retain_redo:
                        self._redoable.append(entry)
                    self._owner.invalidate()

                on_action_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.action_id, error.detail, error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        except FrameworkIntegrityError, FreshActionError:
            raise
        except Exception as error:
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, context.action_id, error=error)
            entry.last_result = result
            self._owner.invalidate()
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
            original.root_action_id,
            f"Compensate {entry.label}",
        )
        execution = CompensationExecution(operation_context, key)
        execution.transition(CompensationStatus.REVERTING)
        intent = CompensationIntent(operation_context, original.action_id, key, datetime.now(UTC))
        entry.compensation_execution = execution
        entry.state = HistoryEntryState.REVERSING
        self._owner.invalidate()
        intent_context = ActionContext.create(
            f"Begin compensation for {entry.label}",
            kind=ActionKind.COMPENSATION,
            cause=operation_context.causal_ref(),
            root_action_id=original.root_action_id,
            compensates_action_id=original.action_id,
        )
        try:
            with fresh_action_transaction(action_context=intent_context):
                participant = getattr(self._compensation_outbox, "participant", None)
                if participant is not None:
                    joined = join_action(
                        (self._compensation_outbox, key),
                        lambda: participant(intent),
                    )
                    assert joined is not None
            claim = await self._compensation_outbox.claim(intent, entry.compensation.retry)
        except asyncio.CancelledError:
            execution.transition(CompensationStatus.CANCELLED)
            entry.state = HistoryEntryState.FAILED
            self._owner.invalidate()
            raise
        except Exception as error:
            execution.transition(CompensationStatus.FAILED, error)
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, error=error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        if not claim.dispatch and claim.status is CompensationStatus.FAILED:
            error = RuntimeError("compensation retry limit exhausted")
            execution.transition(CompensationStatus.FAILED, error)
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, error=error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        if claim.dispatch:
            try:
                await entry.compensation.operation(key)
            except asyncio.CancelledError as error:
                execution.transition(CompensationStatus.CANCELLED)
                entry.state = HistoryEntryState.FAILED
                await self._compensation_outbox.update(
                    intent, CompensationStatus.CANCELLED, ExceptionSummary.capture(error)
                )
                self._owner.invalidate()
                raise
            except Exception as error:
                external_error = error
                try:
                    await self._compensation_outbox.update(
                        intent, CompensationStatus.FAILED, ExceptionSummary.capture(external_error)
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
                self._owner.invalidate()
                return result
            execution.transition(CompensationStatus.EXTERNAL_SUCCEEDED)
            try:
                await self._compensation_outbox.update(intent, CompensationStatus.EXTERNAL_SUCCEEDED)
            except Exception as error:
                execution.transition(CompensationStatus.NEEDS_RECONCILIATION, error)
                entry.state = HistoryEntryState.NEEDS_RECONCILIATION
                result = HistoryResult(HistoryResultStatus.NEEDS_RECONCILIATION, entry, error=error)
                entry.last_result = result
                self._owner.invalidate()
                return result
        compensation_context = ActionContext.create(
            f"Apply compensation for {entry.label}",
            kind=ActionKind.COMPENSATION,
            cause=operation_context.causal_ref(),
            root_action_id=original.root_action_id,
            compensates_action_id=original.action_id,
        )
        result = self._undo_state(entry, retain_redo=False, action_context=compensation_context)
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
                result.action_id,
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
                    result.action_id,
                    error=error,
                )
                entry.last_result = result
                if not any(candidate is entry for candidate in self._undone):
                    self._undone.append(entry)
        self._owner.invalidate()
        return result

    async def redo(self) -> RedoResult:
        """Reapply the inverse of the actual committed undo using its fresh lineage."""
        entry = self._select(self._redoable, None)
        if entry is None or entry.redo_plan is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        entry.state = HistoryEntryState.REAPPLYING
        cause = current_action()
        context = ActionContext.create(
            f"Redo {entry.label}",
            kind=ActionKind.REDO,
            cause=None if cause is None else cause.causal_ref(),
            root_action_id=None if cause is None else cause.root_action_id,
            reapplies_action_id=entry.original_action_id,
        )
        try:
            with fresh_action_transaction(action_context=context):
                planned: list[tuple[object, object]] = []
                if entry.strategy is UndoStrategy.LOCAL_OVERWRITE and entry.redo_plan.participants:
                    detail = ConflictDetail("participant", 0, 0)
                    raise ReactiveConflictError(  # noqa: TRY301
                        detail, "local overwrite policy cannot target transaction participants"
                    )
                for change in entry.redo_plan.participants:
                    inverse = change.token.plan_inverse()
                    if isinstance(inverse, ConflictDetail):
                        raise ReactiveConflictError(  # noqa: TRY301
                            inverse, f"{change.participant_id} cannot be reapplied safely"
                        )
                    planned.append((change.token, inverse))
                if entry.strategy is UndoStrategy.LOCAL_OVERWRITE:
                    apply_local_overwrite_patches(entry.redo_plan.cells)
                else:
                    apply_conditional_patches(entry.redo_plan.cells)
                for token, inverse in planned:
                    token.stage_inverse(inverse)

                def committed(commit: ActionCommit, aftermath: object) -> None:
                    entry.undo_plan = UndoPlan.from_commit(commit)
                    entry.state = HistoryEntryState.READY
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.action_id)
                    entry.last_result = result
                    self._remove_identity(self._redoable, entry)
                    self._undone.append(entry)
                    self._owner.invalidate()

                on_action_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.action_id, error.detail, error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        except FrameworkIntegrityError, FreshActionError:
            raise
        except Exception as error:
            entry.state = HistoryEntryState.FAILED
            result = HistoryResult(HistoryResultStatus.FAILED, entry, context.action_id, error=error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        assert entry.last_result is not None
        return entry.last_result

    def drop_conflicted(self, action_id: object | None = None) -> HistoryEntry | None:
        """Forget a conflicted entry without touching application state."""
        for stack in (self._undone, self._redoable):
            entry = self._select(stack, action_id)
            if entry is not None and entry.state is HistoryEntryState.CONFLICTED:
                self._remove_identity(stack, entry)
                self._owner.invalidate()
                return entry
        return None

    def clear(self) -> None:
        if not (self._undone or self._redoable):
            return
        self._undone.clear()
        self._redoable.clear()
        self._owner.invalidate()

    def _reserve(self, callback) -> None:
        if has_action_hook(self):
            message = "this action already used this history; only one history operation is allowed"
            raise HistoryError(message)
        on_action_commit(callback, key=self)

    def _push(self, entry: HistoryEntry) -> None:
        self._undone.append(entry)
        del self._undone[: max(0, len(self._undone) - self.limit)]
        self._redoable.clear()
        self._owner.invalidate()

    @staticmethod
    def _select(stack: list[HistoryEntry], action_id: object | None) -> HistoryEntry | None:
        if action_id is None:
            return stack[-1] if stack else None
        return next((entry for entry in reversed(stack) if entry.original_action_id == action_id), None)

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

    def __get__(self, instance: HistoryOwner | None, owner: type | None = None) -> History:
        if instance is None:
            return self  # type: ignore[bad-return]
        stack = instance.__dict__.get(self._slot)  # type: ignore[missing-attribute]
        if stack is None:
            stack = History(instance, limit=self._limit, compensation_outbox=self._compensation_outbox)
            instance.__dict__[self._slot] = stack  # type: ignore[missing-attribute]
        return stack


def history(*, limit: int = 20, compensation_outbox: CompensationOutbox | None = None) -> History:
    """Declare an undo stack whose owner lifetime ends the retained inverse plans."""
    return _HistoryField(limit, compensation_outbox)  # type: ignore[bad-return]


def history_actions(stack: History, *, key: str = "history", chrome: Chrome = DEFAULT_CHROME) -> ActionGroup:
    """Build ordinary controls for the history's current LIFO entries."""

    async def undo(event: ActionEvent) -> None:
        await stack.undo()

    async def redo(event: ActionEvent) -> None:
        await stack.redo()

    return action_group(
        action(chrome.undo, undo, key=f"{key}.undo", available=stack.can_undo),
        action(chrome.redo, redo, key=f"{key}.redo", available=stack.can_redo),
        key=key,
    )


def inspect_histories(owner: HistoryOwner) -> tuple[HistorySnapshot, ...]:
    """Inspect declared histories without applying an inverse."""
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
        str(entry.label), entry.recorded_at, str(entry.original_action_id), entry.state.value, conflict
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
    "HistoryOwner",
    "HistoryResult",
    "HistoryResultStatus",
    "HistorySnapshot",
    "MemoryCompensationOutbox",
    "RedoResult",
    "TransactionalCompensationOutbox",
    "UndoPlan",
    "UndoResult",
    "UndoStrategy",
    "history",
    "history_actions",
    "inspect_histories",
]

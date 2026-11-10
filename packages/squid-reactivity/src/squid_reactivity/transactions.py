"""Stage reactive writes and publish validated transactions.

Each transaction owns its read versions, pending cell replacements, participant
changes, and result hooks. Preparation validates the complete overlay before the
commit point; later notifications cannot undo a published result.
"""

import asyncio
import inspect
import logging
import threading
import weakref
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum, auto
from typing import Any, Protocol

from squid_reactivity.actions import (
    ActionCommit,
    ActionContext,
    ActionPurpose,
    ActionRelation,
    ActionResult,
    ActionRollback,
    ActorRef,
    ChangeReport,
    ConflictDetail,
    ExceptionReport,
    ObservedRead,
    RollbackReason,
    TransactionContribution,
    action_scope,
    continuation_callback,
    current_action,
    current_causality,
    in_continuation,
    next_commit_sequence,
)
from squid_reactivity.diagnostics import (
    emit_continuation_failure,
    emit_result,
)
from squid_reactivity.errors import (
    ActionValidationError,
    FrameworkIntegrityError,
    FreshActionError,
    ReactiveConflictError,
    ReactiveWriteError,
    StaleReactiveContextError,
    UndeclaredStateError,
)
from squid_reactivity.tracking import (
    _CURRENT,
    _MISSING,
    ReactiveOwner,
    _active,
    _bump_epoch,
    _Cell,
    _check_write,
    _checkpoint,
    _current_task,
    relaxed_read,
)

_log = logging.getLogger(__name__)


class TransactionView(Protocol):
    """Read-only access to the frozen overlay during participant preparation."""

    def read_staged(self, target: CellTarget) -> SlotValue: ...

    def read_committed(self, target: CellTarget) -> SlotValue: ...

    @property
    def context(self) -> ActionContext: ...


class TransactionParticipant[PreparedT](Protocol):
    """Join an action with writes managed outside the state cells.

    Register through enlist. During commit, prepare validates the staged changes
    and returns the data apply needs. Preparation must not publish changes. If it
    fails, the action aborts before publication.

    Apply runs synchronously after the commit point and must not fail. Finalize
    runs after all participants have applied, when subscribers can read the full
    result. A finalize failure is reported without undoing the commit.
    """

    def prepare(self, view: TransactionView) -> PreparedT:
        """Validate the staged writes and return what `apply` needs, publishing none of them.

        Raise to abort the action: every participant is aborted, component state is
        restored, and the error reaches whoever called the handler.
        """
        ...

    def apply(self, prepared: PreparedT) -> None:
        """Publish the prepared change synchronously. This method must not raise."""

    def describe_change(self, prepared: PreparedT) -> TransactionContribution | None:
        """Return the participant's reversible token, or no history contribution."""

    def abort(self, prepared: PreparedT | None, cause: BaseException) -> None:
        """Discard staged work after failure. Called once in reverse participant order."""

    def finalize(self, prepared: PreparedT) -> None:
        """Notify subscribers after all participants have applied.

        An exception here is reported but does not roll back the committed action.
        """


@dataclass(slots=True)
class _Staged:
    """A pending replacement with a revision for reads inside its transaction.

    Rollback discards the entry. Publication determines committed version lineage
    from the cell being replaced, which may have changed since staging began.
    """

    owner: ReactiveOwner
    name: str
    cell: _Cell
    value: Any
    version: int


@dataclass(frozen=True, slots=True)
class SlotValue:
    """A state slot value that distinguishes absence from a present ``None``."""

    present: bool
    value: Any = None

    @classmethod
    def from_raw(cls, value: Any) -> SlotValue:
        return cls(value is not _MISSING, None if value is _MISSING else value)

    def raw(self) -> Any:
        return self.value if self.present else _MISSING


def _slot_name(cell: _Cell) -> str:
    """Return the shared field address, or an opaque slot ID for local state."""
    return f"slot:{cell.identity}" if cell.address is None else str(cell.address)


@dataclass(frozen=True, slots=True, init=False)
class CellTarget:
    """Refer to a reversible state slot without keeping its owner alive.

    The opaque identity remains available after collection. History can therefore
    report which target expired even when the cell and its readable address are gone.
    """

    _owner: weakref.ReferenceType[ReactiveOwner]
    _cell: weakref.ReferenceType[_Cell]
    name: str
    identity: str

    def __init__(self, owner: ReactiveOwner, name: str, cell: _Cell) -> None:
        object.__setattr__(self, "_owner", weakref.ref(owner))
        object.__setattr__(self, "_cell", weakref.ref(cell))
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "identity", f"slot:{cell.identity}")

    def resolve(self, expected_version: int) -> tuple[ReactiveOwner, _Cell]:
        """Return the live slot or report an expired conditional inverse."""
        owner = self._owner()
        cell = self._cell()
        if owner is None or cell is None:
            detail = ConflictDetail(self.identity, expected_version, -1)
            raise ReactiveConflictError(detail, f"{self.identity} no longer exists; nothing was published")
        return owner, cell

    def live_cell(self) -> _Cell | None:
        """Return the slot for non-mutating diagnostics, or ``None`` after collection."""
        return self._cell()


@dataclass(frozen=True, slots=True)
class CellPatch:
    """One state replacement with exact before/after version lineage."""

    target: CellTarget
    before: SlotValue
    after: SlotValue
    before_version: int
    after_version: int

    def inverse(self) -> ConditionalCellPatch:
        return ConditionalCellPatch(self.target, self.before, self.after_version)


@dataclass(frozen=True, slots=True)
class ConditionalCellPatch:
    """A replacement applicable only while the target has the expected version."""

    target: CellTarget
    value: SlotValue
    expected_version: int


@dataclass(frozen=True, slots=True)
class CellPatchSet:
    """Immutable physical changes from one committed action."""

    patches: tuple[CellPatch, ...] = ()

    def __iter__(self) -> Iterator[CellPatch]:
        return iter(self.patches)

    def __len__(self) -> int:
        return len(self.patches)

    def addresses(self) -> tuple[Any, ...]:
        found: list[Any] = []
        seen: set[int] = set()
        for patch in self.patches:
            cell = patch.target.live_cell()
            address = None if cell is None else cell.address
            if address is None or id(address) in seen:
                continue
            seen.add(id(address))
            found.append(address)
        return tuple(found)

    def inverse(self) -> tuple[ConditionalCellPatch, ...]:
        return tuple(patch.inverse() for patch in reversed(self.patches))


def apply_conditional_patches(patches: Sequence[ConditionalCellPatch]) -> None:
    """Stage an all-or-nothing conditional patch set in the active transaction."""
    current = _CURRENT.get()
    if current is None or not current.writable():
        message = "conditional patches require an active writable transaction"
        raise RuntimeError(message)
    resolved = tuple((patch, *patch.target.resolve(patch.expected_version)) for patch in patches)
    for patch, _, cell in resolved:
        current.require_version(cell, patch.expected_version)
    for patch, owner, cell in resolved:
        _write(owner, patch.target.name, cell, patch.value.raw())


def _write(owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
    """Stage a write into the action in flight, or land it now if there is none.

    `enlist` already returns `None` when no transaction is open; this is the same
    signal read directly. A component built during the action is not protected by it, so
    its construction lands immediately too.
    """
    _check_write(owner, name)
    current = _CURRENT.get()
    if current is None and in_continuation():
        message = "direct reactive mutation in a result callback is forbidden; open a new transaction(...)"
        raise ReactiveWriteError(message)
    if current is not None and not current.writable():
        current.reject_stale(f"{type(owner).__name__}.{name}")
    if current is None or not current.protects(owner):
        cell.write(value)
        owner._state_changed(frozenset((name,)))
        return
    current.stage(owner, name, cell, value)


class Transaction:
    """The identity and eventual outcome of one transaction.

    Nested blocks share this handle. Result is None until the outer block finishes,
    then remains available even if it raised. Hooks run after the outcome is fixed.
    """

    def __init__(self, context: ActionContext) -> None:
        self._context = context
        self._result: ActionResult | None = None

    @property
    def context(self) -> ActionContext:
        return self._context

    @property
    def result(self) -> ActionResult | None:
        return self._result

    def after_commit(self, callback: Callable[[ActionCommit], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_commit(callback, key=key)

    def after_rollback(self, callback: Callable[[ActionRollback], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_rollback(callback, key=key)

    def after_result(self, callback: Callable[[ActionResult], None], *, key: object | None = None) -> None:
        self._check_current()
        on_action_result(callback, key=key)

    def _check_current(self) -> None:
        current = _CURRENT.get()
        if current is None or current.handle is not self:
            message = "callbacks must be registered inside their transaction"
            raise StaleReactiveContextError(message)


class _Phase(Enum):
    """Publication is terminal even if participant apply or later notifications fail."""

    STAGING = auto()
    PREPARING = auto()
    COMMITTED = auto()
    ABORTED = auto()


@dataclass(slots=True)
class _Transaction:
    """Own an overlay until it is discarded or its patch plan is published.

    Preparation freezes writes while retaining overlay reads. Publication closes
    the read view before applying participants and notifying owners; failures
    after that point cannot turn the committed action into a rollback.
    """

    readonly: bool = False
    phase: _Phase = _Phase.STAGING
    owner_task: object | None = None
    """The task that opened this transaction, or `None` outside a running loop."""
    read_view: object = field(default_factory=object)
    """Cache identity without retaining the transaction and its owners."""
    writes: dict[_Cell, _Staged] = field(default_factory=dict)
    observed: dict[_Cell, int] = field(default_factory=dict)
    """Committed versions this action read out of addressed cells, first read per cell.

    Only shared cells are recorded, because only they can move under a running action.
    Recorded whether or not the read carries a precondition, because the ledger reports what
    an action looked at and that is true either way.
    """
    strong: dict[_Cell, int] = field(default_factory=dict)
    """The subset of `observed` taken inside `strong_read()`, which must still hold at commit."""
    preconditions: dict[_Cell, int] = field(default_factory=dict)
    # Identity keys support unhashable owners; values retain them in first-write order.
    changed: dict[int, tuple[ReactiveOwner, set[str]]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    commit_hooks: list[tuple[object | None, Callable[[ActionCommit], None]]] = field(default_factory=list)
    rollback_hooks: list[tuple[object | None, Callable[[ActionRollback], None]]] = field(default_factory=list)
    result_hooks: list[tuple[object | None, Callable[[ActionResult], None]]] = field(default_factory=list)
    context: ActionContext = field(default_factory=ActionContext.create)
    participants: dict[object, TransactionParticipant[Any]] = field(default_factory=dict)
    write_block: str | None = None
    """Why state may not be written right now, while the transaction itself stays open."""
    cleanup_errors: list[ExceptionReport] = field(default_factory=list)
    prepared_participants: list[tuple[TransactionParticipant[Any], Any]] = field(default_factory=list)
    commit_record: ActionCommit | None = None
    handle: Transaction = field(init=False)

    def __post_init__(self) -> None:
        self.handle = Transaction(self.context)

    @property
    def closed(self) -> bool:
        """Terminal transactions no longer expose their overlay, including during notifications."""
        return self.phase in (_Phase.COMMITTED, _Phase.ABORTED)

    def note_born(self, owner: object) -> None:
        """Record an object created after this transaction began."""
        self.born[id(owner)] = owner

    def writable(self) -> bool:
        """Whether this transaction is open and the caller owns it.

        Read-only and temporary write restrictions are checked separately by stage.
        """
        return not self.closed and (self.owner_task is None or self.owner_task is _current_task())

    def reject_stale(self, what: str) -> None:
        """Raise an error identifying the finished transaction or wrong task."""
        if self.closed:
            message = (
                f"{what} was written through a transaction that has already finished. The write "
                f"came from a task that outlived the action which opened it, so nothing would "
                f"publish it: give the task an owner inside the handler, or write in the handler."
            )
        else:
            message = (
                f"{what} was written from a task other than the one that opened its transaction. "
                f"Sibling tasks staging into one overlay make what commits depend on scheduling: "
                f"write from the handler's own task, or give each branch its own transaction."
            )
        raise StaleReactiveContextError(message)

    def protects(self, owner: object) -> bool:
        """Whether this transaction is responsible for the owner's state.

        A transaction restores the view the action started from. An object created during the
        action had no state then, so writing to it is construction, not mutation.
        """
        return id(owner) not in self.born

    def staged(self, cell: _Cell) -> _Staged | None:
        """This action's pending write to `cell`, if it has made one."""
        return self.writes.get(cell)

    def observe(self, cell: _Cell, *, strong: bool) -> None:
        """Record the first committed version read from a shared cell.

        Strong reads have a separate first-read version. An earlier ordinary read
        must not become a precondition just because a later read is strong.
        """
        self.observed.setdefault(cell, cell.version)
        if strong:
            self.strong.setdefault(cell, cell.version)

    def check_preconditions(self) -> None:
        """Check required versions before publishing writes or participant changes.

        Reads of cells this action also writes are checked automatically. Strong
        reads and explicit patch preconditions are checked even when their cells
        are not written. Other reads are recorded for diagnostics only. Blind writes
        have no read precondition, so the last commit wins.
        """
        _checkpoint("commit.before_validation")
        if not (self.writes or self.participants):
            _checkpoint("commit.after_validation")
            return
        required = {cell: version for cell, version in self.observed.items() if cell in self.writes}
        required.update(self.strong)
        required.update(self.preconditions)
        for cell, version in required.items():
            if cell.version == version:
                continue
            detail = ConflictDetail(_slot_name(cell), version, cell.version)
            message = (
                f"{_slot_name(cell)} changed while this action was running: expected version "
                f"{version}, found {cell.version}. Nothing was published."
            )
            raise ReactiveConflictError(detail, message)
        _checkpoint("commit.after_validation")

    def require_version(self, cell: _Cell, version: int) -> None:
        existing = self.preconditions.get(cell)
        if existing is not None and existing != version:
            detail = ConflictDetail(_slot_name(cell), existing, version)
            raise ReactiveConflictError(detail, "incompatible inverse preconditions")
        self.preconditions[cell] = version

    def stage(self, owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
        """Hold a write for commit, visible only to readers of this transaction."""
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot mutate component state"
            raise ReactiveWriteError(message)
        entry = self.writes.get(cell)
        if entry is None:
            self.writes[cell] = _Staged(owner, name, cell, value, cell.version + 1)
        else:
            entry.value = value
            entry.version += 1
        _, names = self.changed.setdefault(id(owner), (owner, set()))
        names.add(name)
        # The staged version is what a read inside this action reports, so a computed that
        # sampled it has to be told the world moved even though nothing is published yet.
        _bump_epoch()

    def publish(self, patches: CellPatchSet) -> None:
        """Install exactly the values and versions recorded in the publication plan."""
        for entry, patch in zip(self.writes.values(), patches, strict=True):
            entry.cell.value = patch.after.raw()
            entry.cell.version = patch.after_version
        self.phase = _Phase.COMMITTED
        if patches:
            _bump_epoch()

    def enlist[ParticipantT: TransactionParticipant[Any]](
        self, key: object, factory: Callable[[], ParticipantT]
    ) -> ParticipantT:
        """Return this action's participant for `key`, creating it on first use.

        The guards run on every call, not just the first: a participant enlisted before a
        `block_writes` region may not keep staging writes inside one.
        """
        if not self.writable():
            self.reject_stale("a transaction participant")
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot stage writes"
            raise ReactiveWriteError(message)
        existing = self.participants.get(key)
        if existing is not None:
            return existing  # type: ignore[bad-return]
        created = factory()
        self.participants[key] = created
        return created

    def patches(self) -> CellPatchSet:
        """Freeze the physical slot lineage written by this action."""
        if not self.writes:
            return CellPatchSet()
        return CellPatchSet(
            tuple(
                CellPatch(
                    CellTarget(entry.owner, entry.name, entry.cell),
                    SlotValue.from_raw(entry.cell.value),
                    SlotValue.from_raw(entry.value),
                    entry.cell.version,
                    max(entry.version, entry.cell.version + 1),
                )
                for entry in self.writes.values()
            )
        )

    def reads(self) -> tuple[ObservedRead, ...]:
        return tuple(ObservedRead(_slot_name(cell), version) for cell, version in self.observed.items())

    @contextmanager
    def preparing(self) -> Iterator[None]:
        """Expose staged values to participants while preventing further writes.

        The transaction context has already been removed before commit. Restore it
        for preparation so participants can read the values they are validating.
        Block writes and new participants, and exclude these internal reads from
        the observation report because conflict validation has already run.
        """
        with (
            _CURRENT.set(self),
            block_writes("a transaction participant cannot write while preparing"),
            relaxed_read(),
        ):
            yield

    def commit(self) -> ActionCommit:
        """Prepare everything, publish synchronously, and return the immutable commit."""
        self.phase = _Phase.PREPARING
        self.check_preconditions()
        view = _FrozenTransactionView(self)
        with self.preparing():
            for participant in self.participants.values():
                _checkpoint("commit.before_participant_prepare")
                self.prepared_participants.append((participant, participant.prepare(view)))
                _checkpoint("commit.after_participant_prepare")
            changes = tuple(
                change
                for participant, value in self.prepared_participants
                if (change := participant.describe_change(value)) is not None
            )
        committed_at = datetime.now(UTC)
        commit = ActionCommit(
            self.context,
            next_commit_sequence(),
            committed_at,
            committed_at - self.context.started_at,
            self.reads(),
            self.patches(),
            changes,
        )
        self.commit_record = commit
        _checkpoint("commit.before_publication")
        self.publish(commit.patches)
        _checkpoint("commit.after_cell_publication")
        # Cell installation begins the infallible publication phase. An adapter that raises
        # from apply has violated the participant contract; rolling cells back could not undo
        # a participant that applied only partly, so preserve the committed diagnostic truth.
        try:
            for participant, value in self.prepared_participants:
                participant.apply(value)
                _checkpoint("commit.after_participant_apply")
        except BaseException:
            _log.critical("an infallible transaction participant apply failed", exc_info=True)
            raise

        return commit

    def finalize_commit(self) -> None:
        """Notify reactive consumers after the commit gate, isolating continuation failures."""
        assert self.commit_record is not None
        if not self.changed and not self.prepared_participants:
            return
        _checkpoint("continuation.before_notification")
        for owner, names in self.changed.values():
            try:
                owner._state_changed(frozenset(names))
            except Exception as error:
                _log.exception("a reactive owner failed to process its committed state")
                emit_continuation_failure(
                    self.commit_record,
                    "notification",
                    f"{type(owner).__qualname__}._state_changed",
                    error,
                )
        for participant, value in self.prepared_participants:
            _checkpoint("continuation.before_participant_finalize")
            try:
                participant.finalize(value)
            except Exception as error:
                _log.exception("a transaction participant failed to finalize")
                emit_continuation_failure(
                    self.commit_record,
                    "participant_finalize",
                    f"{type(participant).__qualname__}.finalize",
                    error,
                )

    def abort(self, cause: BaseException) -> tuple[ExceptionReport, ...]:
        """Discard the overlay and abort each participant once, before publication only."""
        assert self.phase is not _Phase.COMMITTED
        if self.phase is _Phase.ABORTED:
            return tuple(self.cleanup_errors)
        self.phase = _Phase.ABORTED
        _checkpoint("rollback.before_abort")
        values = {id(participant): value for participant, value in self.prepared_participants}
        for participant in reversed(tuple(self.participants.values())):
            try:
                participant.abort(values.get(id(participant)), cause)
            except Exception as error:
                report = ExceptionReport.capture(error)
                self.cleanup_errors.append(report)
                _log.exception("a transaction participant failed to abort")
        for owner, _ in reversed(tuple(self.changed.values())):
            try:
                owner._state_rolled_back()
            except Exception as error:
                report = ExceptionReport.capture(error)
                self.cleanup_errors.append(report)
                _log.exception("a reactive owner failed to process its rolled-back state")
        if self.writes:
            _bump_epoch()
        return tuple(self.cleanup_errors)


@dataclass(frozen=True, slots=True)
class _FrozenTransactionView:
    """The immutable overlay exposed only during participant preparation."""

    transaction: _Transaction

    @property
    def context(self) -> ActionContext:
        return self.transaction.context

    def read_staged(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        staged = self.transaction.staged(cell)
        return SlotValue.from_raw(cell.value if staged is None else staged.value)

    def read_committed(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        return SlotValue.from_raw(cell.value)


def report_undeclared_write(owner: object, name: str) -> None:
    """Reject writes to undeclared attributes during a transaction.

    Those writes cannot be rolled back or trigger reactive updates. Objects
    created inside the transaction are exempt so their constructors can run.
    """
    current = _active()
    if current is None or not current.protects(owner):
        return
    label = f"{type(owner).__name__}.{name}"
    if current.write_block is not None:
        message = f"{current.write_block} ({label})"
        raise ReactiveWriteError(message)
    if current.readonly:
        message = f"parallel-read actions cannot mutate component state ({label})"
        raise ReactiveWriteError(message)
    message = (
        f"{label} was assigned inside a transaction but is not declared state: it would not be "
        f"rolled back if the action failed, and it would not trigger a re-render. "
        f"Declare it with state()."
    )
    raise UndeclaredStateError(message)


_COMMIT_GATE = threading.RLock()


def _rollback_reason(error: BaseException, *, during_commit: bool) -> RollbackReason:
    if isinstance(error, asyncio.CancelledError):
        return RollbackReason.CANCELLED
    if isinstance(error, ReactiveConflictError):
        return RollbackReason.CONFLICT
    if isinstance(error, ActionValidationError):
        return RollbackReason.VALIDATION_FAILED
    if during_commit:
        return RollbackReason.PARTICIPANT_PREPARE_FAILED
    return RollbackReason.HANDLER_EXCEPTION


def _notify_hooks(
    result: ActionResult,
    hooks: Sequence[tuple[object | None, Callable[[Any], None]]],
) -> None:
    if not hooks:
        return
    with continuation_callback():
        for _, callback in hooks:
            _checkpoint("continuation.before_hook")
            try:
                callback_result = callback(result)
                if inspect.isawaitable(callback_result):
                    if inspect.iscoroutine(callback_result):
                        callback_result.close()
                    message = "action continuation hooks must be synchronous; start an operation instead"
                    raise TypeError(message)  # noqa: TRY301
            except Exception as error:
                _log.exception("an action continuation hook failed")
                emit_continuation_failure(
                    result,
                    "hook",
                    getattr(callback, "__qualname__", type(callback).__qualname__),
                    error,
                )


def _emit_commit(current: _Transaction, commit: ActionCommit) -> None:
    current.handle._result = commit
    emit_result(commit)
    _notify_hooks(commit, current.commit_hooks)
    _notify_hooks(commit, current.result_hooks)


def _emit_rollback(current: _Transaction, error: BaseException, *, during_commit: bool) -> ActionRollback:
    cleanup_errors = current.abort(error)
    rolled_back_at = datetime.now(UTC)
    rollback = ActionRollback(
        current.context,
        rolled_back_at,
        rolled_back_at - current.context.started_at,
        _rollback_reason(error, during_commit=during_commit),
        current.reads(),
        error.detail if isinstance(error, ReactiveConflictError) else None,
        ExceptionReport.capture(error),
        ChangeReport(len(current.writes), len(current.participants)),
        cleanup_errors,
    )
    current.handle._result = rollback
    emit_result(rollback)
    _notify_hooks(rollback, current.rollback_hooks)
    _notify_hooks(rollback, current.result_hooks)
    return rollback


@contextmanager
def _run_transaction(context: ActionContext, *, readonly: bool = False) -> Iterator[Transaction]:
    """Own staging, the commit point, and outcome delivery for either action mode."""
    current = _Transaction(readonly=readonly, owner_task=_current_task(), context=context)
    with action_scope(context):
        token = _CURRENT.set(current)
        try:
            _checkpoint("transaction.enter_body")
            yield current.handle
        except BaseException as error:
            _CURRENT.reset(token)
            _emit_rollback(current, error, during_commit=False)
            raise
        _CURRENT.reset(token)
        try:
            _checkpoint("transaction.close_staging")
            with _COMMIT_GATE:
                commit = current.commit()
        except BaseException as error:
            if current.phase is _Phase.COMMITTED:
                # Publication is the commit point. An apply failure is an adapter integrity
                # defect, not a user rollback; retain the commit truth and fail loudly.
                assert current.commit_record is not None
                integrity_commit = replace(
                    current.commit_record,
                    tags=frozenset({RollbackReason.FRAMEWORK_INTEGRITY_FAILURE.value}),
                )
                current.handle._result = integrity_commit
                emit_result(integrity_commit)
                message = f"an infallible transaction adapter failed after the commit point: {error}"
                raise FrameworkIntegrityError(message) from error
            else:
                _emit_rollback(current, error, during_commit=True)
            raise
        try:
            _checkpoint("commit.gate_released")
            current.finalize_commit()
        finally:
            _emit_commit(current, commit)


@contextmanager
def transaction(
    name: str | None = None,
    *,
    actor: ActorRef | None = None,
    caused_by: ActionContext | None = None,
    kind: ActionPurpose = ActionPurpose.NORMAL,
    relation: ActionRelation | None = None,
    action_context: ActionContext | None = None,
) -> Iterator[Transaction]:
    """Stage state changes and publish them together when the context exits.

    An exception in the body or preparation discards staged changes. Nested
    calls share the outer transaction; they are not independent savepoints.
    Only the task that opened the transaction may write through it. The yielded
    handle exposes the final result and registers outcome callbacks. Supply
    caused_by to link a new action to an earlier one. Identity arguments apply
    only when opening an outer transaction.

    Participants must not fail during apply, which runs after the commit point.
    Such a failure raises FrameworkIntegrityError and is recorded as a damaged
    commit, since already applied external changes cannot be rolled back here.
    """
    outer = _CURRENT.get()
    if outer is not None:
        # Checked at the `with`, not at the first write inside it, so a task that inherited
        # someone else's transaction is told where it went wrong rather than where it showed.
        if not outer.writable():
            outer.reject_stale("a nested transaction")
        yield outer.handle
        return
    explicit_identity = (
        name is not None
        or actor is not None
        or caused_by is not None
        or kind is not ActionPurpose.NORMAL
        or relation is not None
    )
    if action_context is not None and explicit_identity:
        message = "supply either action_context or transaction identity arguments"
        raise ValueError(message)
    causality = current_causality() if caused_by is None else (caused_by.causal_ref(), caused_by.root_action_id)
    context = (
        action_context
        or (current_action() if not explicit_identity and not in_continuation() else None)
        or ActionContext.create(
            "action" if name is None else name,
            actor=actor,
            relation=relation,
            kind=kind,
            cause=None if causality is None else causality[0],
            root_action_id=None if causality is None else causality[1],
        )
    )
    with _run_transaction(context) as handle:
        yield handle


@contextmanager
def fresh_action_transaction(*, action_context: ActionContext) -> Iterator[Transaction]:
    """Start a distinct causal transaction while an empty admitting transaction is open.

    Mounted undo/redo handlers are admitted inside the dispatch transaction. Suspending that
    empty envelope lets the history operation remain a real action with its own identity. An
    outer transaction that already staged work is rejected because committing the inner action
    could not then be rolled back with the outer one.
    """
    outer = _CURRENT.get()
    if outer is not None and (outer.writes or outer.participants or outer.preconditions):
        message = "a fresh action cannot start after the admitting transaction staged changes"
        raise FreshActionError(message)
    with _CURRENT.set(None), transaction(action_context=action_context) as handle:
        yield handle


@contextmanager
def batch() -> Iterator[None]:
    """Coalesce related state writes into one invalidation per component."""
    with transaction():
        yield


@contextmanager
def readonly_transaction(*, action_context: ActionContext | None = None) -> Iterator[Transaction]:
    """Run an identified read-only action and reject any state mutation."""
    if _CURRENT.get() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    causality = current_causality()
    context = (
        action_context
        or current_action()
        or ActionContext.create(
            "read-only action",
            cause=None if causality is None else causality[0],
            root_action_id=None if causality is None else causality[1],
        )
    )
    with _run_transaction(context, readonly=True) as handle:
        yield handle


def on_action_commit(callback: Callable[[ActionCommit], None], *, key: object | None = None) -> None:
    """Register a synchronous callback for a successful commit.

    Call inside a transaction. Callback failures are reported without changing
    the result. Open a new transaction for writes; caused_by=result.context links it to this action.
    """
    _add_action_hook("commit", callback, key=key)


def on_action_rollback(callback: Callable[[ActionRollback], None], *, key: object | None = None) -> None:
    """Register a synchronous callback after rollback; its errors are reported."""
    _add_action_hook("rollback", callback, key=key)


def on_action_result(callback: Callable[[ActionResult], None], *, key: object | None = None) -> None:
    """Register a synchronous callback for either commit or rollback."""
    _add_action_hook("result", callback, key=key)


def _add_action_hook(kind: str, callback: Callable[..., None], *, key: object | None) -> None:
    current = _CURRENT.get()
    if current is None:
        message = "commit hooks are only available inside an action's transaction"
        raise RuntimeError(message)
    if not current.writable():
        current.reject_stale("a commit hook")
    if key is not None and has_action_hook(key):
        message = f"{key!r} already registered a commit hook for this action"
        raise RuntimeError(message)
    hooks = {
        "commit": current.commit_hooks,
        "rollback": current.rollback_hooks,
        "result": current.result_hooks,
    }[kind]
    hooks.append((key, callback))


def enlist[ParticipantT: TransactionParticipant[Any]](
    key: object, factory: Callable[[], ParticipantT]
) -> ParticipantT | None:
    """Take part in the action in flight, staging writes instead of publishing them.

    Returns the participant registered for `key`, built by `factory` on first use, or
    `None` when no transaction is open -- the caller's signal that its write has nothing
    to wait for and should land now. `key` identifies the subsystem, is usually the
    subsystem itself, and must be hashable.
    """
    current = _CURRENT.get()
    if current is None:
        return None
    return current.enlist(key, factory)


def action_participant(key: object) -> TransactionParticipant[Any] | None:
    """Return the participant already registered for ``key``, without creating one."""
    current = _CURRENT.get()
    return None if current is None else current.participants.get(key)


def has_action_hook(key: object) -> bool:
    """Whether `key` already registered any result hook for the action in flight."""
    current = _CURRENT.get()
    if current is None:
        return False
    return any(
        registered is key
        for hooks in (current.commit_hooks, current.rollback_hooks, current.result_hooks)
        for registered, _ in hooks
    )


@contextmanager
def block_writes(reason: str) -> Iterator[None]:
    """Reject component-state writes for the duration of the block.

    Narrower than `readonly_transaction`: the transaction stays open and keeps everything it
    has recorded. For code that runs *inside* an action but must not touch the tree, such as
    an undo entry's external inverse, whose writes a following restore would silently clobber.
    """
    current = _CURRENT.get()
    if current is None:
        yield
        return
    previous = current.write_block
    current.write_block = reason
    try:
        yield
    finally:
        current.write_block = previous

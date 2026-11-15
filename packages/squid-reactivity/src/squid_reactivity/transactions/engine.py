"""Validate and publish one transaction's staged changes, then deliver its outcome.

The engine keeps pending values separate from committed cells. It checks version
conditions, prepares participants, publishes cells and participant changes, then
notifies consumers. Before publication a failure discards staged work; afterward
the result remains committed even if a notification fails.
"""

import asyncio
import inspect
import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum, auto
from threading import Thread, current_thread
from typing import TYPE_CHECKING, Any, Protocol, cast, override

from squid_reactivity._runtime import _bump_epoch, _runtime
from squid_reactivity.cells import _Cell, _Missing
from squid_reactivity.diagnostics import (
    emit_causal_event,
    emit_continuation_failure,
    emit_result,
)
from squid_reactivity.diagnostics.observers import has_validation_observers
from squid_reactivity.diagnostics.snapshots import PreconditionSnapshot, TransactionValidationSnapshot
from squid_reactivity.errors import (
    FrameworkIntegrityError,
    ReactiveConflictError,
    ReactiveWriteError,
    StaleReactiveContextError,
    TransactionValidationError,
    UndeclaredStateError,
)
from squid_reactivity.transactions.context import (
    ExceptionReport,
    TransactionContext,
    continuation_callback,
    in_continuation,
)
from squid_reactivity.transactions.patches import CellPatch, CellPatchSet, CellTarget, SlotValue
from squid_reactivity.transactions.results import (
    ChangeReport,
    CommitSequence,
    ConflictDetail,
    RollbackReason,
    TransactionCommit,
    TransactionContribution,
    TransactionResult,
    TransactionRollback,
)
from squid_reactivity.transactions.state import (
    _CURRENT_TRANSACTION,
    ReactiveStateOwner,
    _active,
    _check_write,
    _checkpoint,
    _current_task,
    _current_transaction,
    block_writes,
    relaxed_read,
)

if TYPE_CHECKING:
    from squid_reactivity.transactions.scope import Transaction

_log = logging.getLogger(__name__)


class TransactionView(Protocol):
    """Read-only access to the frozen overlay during participant preparation."""

    __slots__ = ()

    @property
    def context(self) -> TransactionContext: ...

    def read_staged(self, target: CellTarget) -> SlotValue: ...

    def read_committed(self, target: CellTarget) -> SlotValue: ...


class TransactionParticipant[PreparedT](Protocol):
    """Join a transaction with writes managed outside the state cells.

    Register through enlist. During commit, prepare validates the staged changes
    and returns the data apply needs. Preparation must not publish changes. If it
    fails, the transaction aborts before publication.

    Apply runs synchronously after the commit point and must not fail. Finalize
    runs after all participants have applied, when subscribers can read the full
    result. A finalize failure is reported without undoing the commit.
    """

    __slots__ = ()

    def prepare(self, view: TransactionView) -> PreparedT:
        """Validate pending work and return the data apply() needs.

        Raise to stop the transaction before publication. The engine aborts participants,
        discards staged cell writes, and propagates the error to the transaction caller.
        """
        ...

    def describe_change(self, prepared: PreparedT) -> TransactionContribution | None:
        """Return the participant's reversible token, or no history contribution."""

    def apply(self, prepared: PreparedT) -> None:
        """Publish the prepared change synchronously. This method must not raise."""

    def finalize(self, prepared: PreparedT) -> None:
        """Notify subscribers after all participants have applied.

        An exception here is reported but does not roll back the committed transaction.
        """

    def abort(self, prepared: PreparedT | None, cause: BaseException) -> None:
        """Discard staged work after failure. Called once in reverse participant order."""


@dataclass(slots=True)
class _Staged[ValueT]:
    """A pending replacement with a revision for reads inside its transaction.

    Rollback discards the entry. Publication determines committed version lineage
    from the cell being replaced, which may have changed since staging began.
    """

    owner: ReactiveStateOwner
    name: str
    cell: _Cell[ValueT]
    value: ValueT | _Missing
    version: int


class _Phase(Enum):
    """Publication is terminal even if participant apply or later notifications fail."""

    STAGING = auto()
    PREPARING = auto()
    COMMITTED = auto()
    ABORTED = auto()


@dataclass(slots=True)
class _TransactionEngine:
    """Own an overlay until it is discarded or its patch plan is published.

    Preparation freezes writes while retaining overlay reads. Publication closes
    the read view before applying participants and notifying owners; failures
    after that point cannot turn the committed transaction into a rollback.
    """

    handle: Transaction
    phase: _Phase = _Phase.STAGING
    owner_thread: Thread = field(default_factory=current_thread)
    owner_task: object | None = None
    """The task that opened this transaction, or `None` outside a running loop."""
    read_view: object = field(default_factory=object)
    """Cache identity without retaining the transaction and its owners."""
    writes: dict[_Cell[Any], _Staged[Any]] = field(default_factory=dict)
    observed: dict[_Cell[Any], int] = field(default_factory=dict)
    """Committed versions this transaction read out of addressed cells, first read per cell.

    Keep reads even before their cells are written: a later write makes the first
    observed version a commit precondition.
    """
    strong: dict[_Cell[Any], int] = field(default_factory=dict)
    """The subset of `observed` taken inside `strong_read()`, which must still hold at commit."""
    preconditions: dict[_Cell[Any], int] = field(default_factory=dict)
    # Identity keys support unhashable owners; values retain them in first-write order.
    changed: dict[int, tuple[ReactiveStateOwner, set[str]]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    commit_hooks: list[tuple[object | None, Callable[[TransactionCommit], None]]] = field(default_factory=list)
    rollback_hooks: list[tuple[object | None, Callable[[TransactionRollback], None]]] = field(default_factory=list)
    result_hooks: list[tuple[object | None, Callable[[TransactionResult], None]]] = field(default_factory=list)
    participants: dict[object, TransactionParticipant[Any]] = field(default_factory=dict)
    write_block: str | None = None
    """Why state may not be written right now, while the transaction itself stays open."""
    cleanup_errors: list[ExceptionReport] = field(default_factory=list)
    prepared_participants: list[tuple[TransactionParticipant[Any], Any]] = field(default_factory=list)
    commit_record: TransactionCommit | None = None
    validation_snapshot: TransactionValidationSnapshot | None = None

    @property
    def context(self) -> TransactionContext:
        return self.handle.context

    @property
    def readonly(self) -> bool:
        return self.handle.readonly

    @property
    def closed(self) -> bool:
        """Terminal transactions no longer expose their overlay, including during notifications."""
        return self.phase in (_Phase.COMMITTED, _Phase.ABORTED)

    def writable(self) -> bool:
        """Whether this transaction is open and the caller owns it.

        Read-only and temporary write restrictions are checked separately by stage.
        """
        return (
            self.owner_thread is current_thread()
            and not self.closed
            and (self.owner_task is None or self.owner_task is _current_task())
        )

    def reject_stale(self, what: str) -> None:
        """Raise an error identifying the finished transaction or wrong task."""
        if self.closed:
            message = (
                f"{what} was written through a transaction that has already finished. The write "
                f"came from a task that outlived the transaction which opened it, so nothing would "
                f"publish it: give the task an owner inside the handler, or write in the handler."
            )
        else:
            message = (
                f"{what} was written from a task other than the one that opened its transaction. "
                f"Sibling tasks staging into one overlay make what commits depend on scheduling: "
                f"write from the handler's own task, or give each branch its own transaction."
            )
        raise StaleReactiveContextError(message)

    def note_born(self, owner: object) -> None:
        """Record an object created after this transaction began."""
        self.born[id(owner)] = owner

    def protects(self, owner: object) -> bool:
        """Whether writes to this object belong in the transaction's overlay.

        Objects created after the transaction began have no earlier state to restore.
        Their initialization writes are applied immediately instead.
        """
        return id(owner) not in self.born

    def staged[ValueT](self, cell: _Cell[ValueT]) -> _Staged[ValueT] | None:
        """This transaction's pending write to `cell`, if it has made one."""
        # stage() stores each value under its matching cell; the dictionary erases
        # their shared type only so it can hold fields of different types.
        return cast(_Staged[ValueT] | None, self.writes.get(cell))

    def observe(self, cell: _Cell[Any], *, strong: bool) -> None:
        """Record the first committed version read from a shared cell.

        Strong reads have a separate first-read version. An earlier ordinary read
        must not become a precondition just because a later read is strong.
        """
        self.observed.setdefault(cell, cell.version)
        if strong:
            self.strong.setdefault(cell, cell.version)

    def require_version(self, cell: _Cell[Any], version: int) -> None:
        existing = self.preconditions.get(cell)
        if existing is not None and existing != version:
            detail = ConflictDetail(cell.label, existing, version)
            raise ReactiveConflictError(detail, "incompatible inverse preconditions")
        self.preconditions[cell] = version

    def stage[ValueT](
        self, owner: ReactiveStateOwner, name: str, cell: _Cell[ValueT], value: ValueT | _Missing
    ) -> None:
        """Hold a write for commit, visible only to readers of this transaction."""
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "read-only transactions cannot mutate component state"
            raise ReactiveWriteError(message)
        entry = self.staged(cell)
        if entry is None:
            self.writes[cell] = _Staged(owner, name, cell, value, cell.version + 1)
        else:
            entry.value = value
            entry.version += 1
        _, names = self.changed.setdefault(id(owner), (owner, set()))
        names.add(name)
        # The staged version is what a read inside this transaction reports, so a computed that
        # sampled it has to be told the world moved even though nothing is published yet.
        _bump_epoch()

    def enlist[ParticipantT: TransactionParticipant[Any]](
        self, key: object, factory: Callable[[], ParticipantT]
    ) -> ParticipantT:
        """Return this transaction's participant for `key`, creating it on first use.

        The guards run on every call, not just the first: a participant enlisted before a
        `block_writes` region may not keep staging writes inside one.
        """
        if not self.writable():
            self.reject_stale("a transaction participant")
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "read-only transactions cannot stage writes"
            raise ReactiveWriteError(message)
        existing = self.participants.get(key)
        if existing is not None:
            return existing  # type: ignore[bad-return]
        created = factory()
        self.participants[key] = created
        return created

    def add_commit_hook(self, callback: Callable[[TransactionCommit], None], *, key: object | None) -> None:
        self._add_hook(self.commit_hooks, callback, key=key)

    def add_rollback_hook(self, callback: Callable[[TransactionRollback], None], *, key: object | None) -> None:
        self._add_hook(self.rollback_hooks, callback, key=key)

    def add_result_hook(self, callback: Callable[[TransactionResult], None], *, key: object | None) -> None:
        self._add_hook(self.result_hooks, callback, key=key)

    def has_hook(self, key: object) -> bool:
        return any(
            registered is key
            for hooks in (self.commit_hooks, self.rollback_hooks, self.result_hooks)
            for registered, _ in hooks
        )

    def _add_hook[ResultT: TransactionResult](
        self,
        hooks: list[tuple[object | None, Callable[[ResultT], None]]],
        callback: Callable[[ResultT], None],
        *,
        key: object | None,
    ) -> None:
        if not self.writable():
            self.reject_stale("a result hook")
        if key is not None and self.has_hook(key):
            message = f"{key!r} already registered a result hook for this transaction"
            raise RuntimeError(message)
        hooks.append((key, callback))

    def finish(self) -> None:
        """Publish staged work and deliver its outcome, preserving the commit on later failure."""
        try:
            _checkpoint("transaction.close_staging")
            commit = self.commit()
        except BaseException as error:
            if self.phase is _Phase.COMMITTED:
                # Publication is the commit point. An apply failure is an adapter integrity
                # defect, not a user rollback; retain the commit truth and fail loudly.
                assert self.commit_record is not None
                integrity_commit = replace(
                    self.commit_record,
                    tags=frozenset({RollbackReason.FRAMEWORK_INTEGRITY_FAILURE.value}),
                )
                self.handle._result = integrity_commit
                self._deliver_result(integrity_commit)
                message = f"an infallible transaction adapter failed after the commit point: {error}"
                raise FrameworkIntegrityError(message) from error
            self.rollback(error, during_commit=True)
            raise
        try:
            _checkpoint("commit.before_notifications")
            self.finalize_commit()
        finally:
            self._emit_commit(commit)

    def check_preconditions(self) -> None:
        """Check required versions before publishing writes or participant changes.

        Reads of cells this transaction also writes are checked automatically. Strong
        reads and explicit patch preconditions are checked even when their cells
        are not written. Ordinary reads of cells that remain unwritten impose no
        precondition. Blind writes have no read precondition, so the last commit wins.
        """
        _checkpoint("commit.before_validation")
        required: dict[_Cell[Any], int] = {}
        if self.writes or self.participants:
            required = {cell: version for cell, version in self.observed.items() if cell in self.writes}
            required.update(self.strong)
            required.update(self.preconditions)
        if has_validation_observers():
            self.validation_snapshot = TransactionValidationSnapshot(
                str(self.context.transaction_id),
                str(self.context.root_transaction_id),
                self.context.cause,
                datetime.now(UTC),
                tuple(
                    PreconditionSnapshot(
                        f"slot:{cell.identity}",
                        version,
                        cell.version,
                        "explicit"
                        if cell in self.preconditions
                        else "strong_read"
                        if cell in self.strong
                        else "read_write",
                    )
                    for cell, version in required.items()
                ),
            )
        for cell, version in required.items():
            if cell.version == version:
                continue
            detail = ConflictDetail(cell.label, version, cell.version)
            message = (
                f"{cell.label} changed while this transaction was running: expected version "
                f"{version}, found {cell.version}. Nothing was published."
            )
            raise ReactiveConflictError(detail, message)
        _checkpoint("commit.after_validation")

    @contextmanager
    def preparing(self) -> Iterator[None]:
        """Expose staged values to participants while preventing further writes.

        The transaction context has already been removed before commit. Restore it
        for preparation so participants can read the values they are validating.
        Block writes and new participants, and skip recording these internal reads
        because conflict validation has already run.
        """
        with (
            _CURRENT_TRANSACTION.set(self),
            block_writes("a transaction participant cannot write while preparing"),
            relaxed_read(),
        ):
            yield

    def patches(self) -> CellPatchSet:
        """Freeze the physical slot lineage written by this transaction."""
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

    def commit(self) -> TransactionCommit:
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
        commit = TransactionCommit(
            self.context,
            _next_commit_sequence(),
            committed_at,
            committed_at - self.context.started_at,
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

    def publish(self, patches: CellPatchSet) -> None:
        """Install exactly the values and versions recorded in the publication plan."""
        for entry, patch in zip(self.writes.values(), patches, strict=True):
            entry.cell.value = patch.after.raw()
            entry.cell.version = patch.after_version
        self.phase = _Phase.COMMITTED
        if patches:
            _bump_epoch()

    def finalize_commit(self) -> None:
        """Notify reactive consumers after publication, isolating continuation failures."""
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

    def rollback(self, error: BaseException, *, during_commit: bool = False) -> TransactionRollback:
        """Discard staged work, record the failure, and deliver rollback callbacks."""
        cleanup_errors = self.abort(error)
        rolled_back_at = datetime.now(UTC)
        rollback = TransactionRollback(
            self.context,
            rolled_back_at,
            rolled_back_at - self.context.started_at,
            _rollback_reason(error, during_commit=during_commit),
            error.detail if isinstance(error, ReactiveConflictError) else None,
            ExceptionReport.capture(error),
            ChangeReport(len(self.writes), len(self.participants)),
            cleanup_errors,
        )
        self.handle._result = rollback
        self._deliver_result(rollback)
        _notify_hooks(rollback, self.rollback_hooks)
        _notify_hooks(rollback, self.result_hooks)
        return rollback

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

    def _emit_commit(self, commit: TransactionCommit) -> None:
        self.handle._result = commit
        self._deliver_result(commit)
        _notify_hooks(commit, self.commit_hooks)
        _notify_hooks(commit, self.result_hooks)

    def _deliver_result(self, result: TransactionResult) -> None:
        emit_result(result)
        # Sinks may run application code, so validation diagnostics are delivered
        # only once the outcome is fixed, never between validation and publication.
        if self.validation_snapshot is not None:
            emit_causal_event(self.validation_snapshot)


@dataclass(frozen=True, slots=True)
class _FrozenTransactionView(TransactionView):
    """Expose staged and committed values while participant preparation freezes writes.

    This is a read-only view of the engine's overlay, not a copy of cell values.
    Participants should use it only during prepare().
    """

    transaction: _TransactionEngine

    @property
    @override
    def context(self) -> TransactionContext:
        return self.transaction.context

    @override
    def read_staged(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        staged = self.transaction.staged(cell)
        return SlotValue.from_raw(cell.value if staged is None else staged.value)

    @override
    def read_committed(self, target: CellTarget) -> SlotValue:
        _, cell = target.resolve(-1)
        return SlotValue.from_raw(cell.value)


def _write[ValueT](owner: ReactiveStateOwner, name: str, cell: _Cell[ValueT], value: ValueT | _Missing) -> None:
    """Stage a field write, or publish it immediately outside a transaction.

    Writes to objects created inside the transaction also publish immediately: those
    objects had no earlier state for the transaction to restore.
    """
    _check_write(owner, name)
    current = _current_transaction()
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
        message = f"read-only transactions cannot mutate component state ({label})"
        raise ReactiveWriteError(message)
    message = (
        f"{label} was assigned inside a transaction but is not declared state: it would not be "
        f"rolled back if the transaction failed, and it would not trigger a re-render. "
        f"Declare it with state()."
    )
    raise UndeclaredStateError(message)


def _next_commit_sequence() -> CommitSequence:
    _runtime.sequence += 1
    return CommitSequence(_runtime.identity, _runtime.sequence)


def _rollback_reason(error: BaseException, *, during_commit: bool) -> RollbackReason:
    if isinstance(error, asyncio.CancelledError):
        return RollbackReason.CANCELLED
    if isinstance(error, ReactiveConflictError):
        return RollbackReason.CONFLICT
    if isinstance(error, TransactionValidationError):
        return RollbackReason.VALIDATION_FAILED
    if during_commit:
        return RollbackReason.PARTICIPANT_PREPARE_FAILED
    return RollbackReason.HANDLER_EXCEPTION


def _notify_hooks(
    result: TransactionResult,
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
                    message = "transaction continuation hooks must be synchronous; start an operation instead"
                    raise TypeError(message)  # noqa: TRY301
            except Exception as error:
                _log.exception("a transaction continuation hook failed")
                emit_continuation_failure(
                    result,
                    "hook",
                    getattr(callback, "__qualname__", type(callback).__qualname__),
                    error,
                )

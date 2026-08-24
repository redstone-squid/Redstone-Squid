"""Transactional reactive state for component trees.

A state field stores an immutable value in a :class:`_Cell`, next to the version that dates
it. Writing replaces the value; nothing is mutated in place. That is what makes a snapshot a
reference rather than a deep copy, and rolling an action back putting the old reference back.

Reads are tracked. A computed records the cells it read and the version each held, and asked
for its value it recomputes only if one of those versions has moved. Nothing is pushed: every
reference points from reader to source, which is what lets a per-message component be
collected while the state it read lives on.
"""

import asyncio
import functools
import inspect
import logging
import threading
from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, ClassVar, Protocol, Self, overload

from squid_reactive.actions import (
    ActionCommit,
    ActionContext,
    ActionOutcome,
    ActionRollback,
    Aftermath,
    ChangeSummary,
    ConflictDetail,
    ExceptionSummary,
    ObservedRead,
    ParticipantChange,
    RollbackReason,
    action_scope,
    aftermath_callback,
    current_action,
    elapsed,
    emit_outcome,
    in_aftermath,
    next_commit_sequence,
)

_log = logging.getLogger(__name__)

_MISSING = object()
"""A cell that has never been assigned. Distinct from ``None``, which is a real value."""


class ReactiveOwner(Protocol):
    __dict__: dict[str, Any]

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


class TransactionView(Protocol):
    """Read-only access to the frozen overlay during participant preparation."""

    def read_staged(self, target: CellTarget) -> SlotValue: ...

    def read_committed(self, target: CellTarget) -> SlotValue: ...

    @property
    def context(self) -> ActionContext: ...


class ActionParticipant[PreparedT](Protocol):
    """A subsystem that publishes its own writes when the action in flight commits.

    The split is the whole point. Everything that can fail happens in `prepare`, before
    any participant has made anything visible, so the transaction can still roll the
    action back as though it never ran. Register with :func:`join_action`.

    `prepare` hands what it built to `apply` as a value, rather than leaving it in a field
    `apply` has to trust. That is what makes "everything fallible happened in prepare" a
    fact of the signatures instead of an assertion inside a body.
    """

    def prepare(self, view: TransactionView) -> PreparedT:
        """Validate the staged writes and return what `apply` needs, publishing none of them.

        Raise to abort the action: every participant is aborted, component state is
        restored, and the error reaches whoever called the handler.
        """

    def apply(self, prepared: PreparedT) -> None:
        """Publish what `prepare` returned. Synchronous, and past the point of failure."""

    def describe_change(self, prepared: PreparedT) -> ParticipantChange | None:
        """Return the participant's reversible token, or no history contribution."""

    def abort(self, prepared: PreparedT | None, cause: BaseException) -> None:
        """Discard staged work after failure. Called once in reverse participant order."""

    def finalize(self, prepared: PreparedT) -> None:
        """React to a commit that has fully landed -- notify watchers, publish addresses.

        Every participant has applied by now, so a subscriber this wakes cannot observe a
        half-published action. Raising here does not undo the commit; it already happened.
        """


class ReactiveWriteError(RuntimeError):
    """A state mutation was attempted inside a read-only action."""


class UndeclaredStateError(RuntimeError):
    """An attribute that is not declared state was written inside a transaction."""


class ReactiveCycleError(RuntimeError):
    """A derived value depends, through some chain, on itself.

    Raised for a computed that reads itself and for a resource whose loader awaits one
    waiting on it -- the same mistake, and a chain can cross both kinds, so it is one error.
    Carries the whole ring rather than the node that closed it, because `a` needing `b` is
    usually fine and `b` needing `a` is usually fine, and only the pair is wrong.
    """

    def __init__(self, path: Sequence[str]) -> None:
        self.path = tuple(path)
        joined = " -> ".join(self.path)
        message = f"cycle: {joined}. Nothing in that ring can settle, because each waits on the next."
        super().__init__(message)


class StaleReactiveContextError(RuntimeError):
    """A transaction was written through from outside the scope or the task that owns it.

    Two mistakes with one shape, because a transaction travels in a `ContextVar` and a task
    inherits a copy of it. A task spawned inside a handler still holds the handler's
    transaction after that `with` block has committed, so a write through it stages into an
    action that is over. Sibling tasks under one `gather` hold the *same* transaction, and
    interleaving their staging into one overlay makes what commits a matter of scheduling.

    Reads are left alone either way: a closed transaction reads as though absent, which is
    what its cells already say, and reading an open one from a branch is what branches do.
    """


class ReactiveConflictError(RuntimeError):
    """An action's strong input or explicit inverse precondition moved before commit.

    Nothing was published: the action rolls back whole and travels the ordinary failed-handler
    path. A handler cannot catch this, because the check runs when its transaction exits.
    """

    def __init__(self, detail: ConflictDetail, message: str) -> None:
        self.detail = detail
        super().__init__(message)


def _equal(left: Any, right: Any) -> bool:
    """Whether a write leaves the field where it already was, conservatively."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


def _frozen(value: Any) -> Any:
    """Return a restored snapshot value in the shape it was exported from.

    JSON has one sequence type, so an exported tuple comes back as a list. A field declared
    `Sequence` reads either; one declared `tuple[...]` needs the tuple back.
    """
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_reactive_transaction", default=None)
_RELAXED_READS: ContextVar[int] = ContextVar("squid_reactive_relaxed_reads", default=0)


def _current_task() -> object | None:
    """The task a write is happening on, or `None` when nothing is running one.

    Synchronous use has no tasks to confuse, so it reports `None` and every transaction
    counts as confined to it.
    """
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _active() -> _Transaction | None:
    """The transaction a *read* should consult: the open one, or none at all.

    A closed transaction is not an error to read through -- it is simply over, and the cells
    now hold what it decided. Answering `None` for one is what makes an inherited copy
    harmless to a reader while `_write` stays loud about it.
    """
    current = _CURRENT.get()
    return None if current is None or current.closed else current


class _Cell:
    """One state field's storage: an immutable value, and the version that dates it.

    `address` is what an addressed cell publishes under: a ``CellAddress`` for shared state,
    or a ``Topic`` for the valueless cell behind :func:`squid_reactive.watch`. A local owner's
    cell has none, and the two behaviours an address adds --
    the commit precondition below, and being followed by a render -- both key off its
    presence rather than off a second cell type.
    """

    __slots__ = ("address", "value", "version")

    def __init__(self, value: Any = _MISSING, version: int = 0, address: Any = None) -> None:
        self.value = value
        self.version = version
        self.address = address

    def settle(self) -> int:
        """Return the version a reader should compare against. A cell is always settled."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                return entry.version
        return self.version

    def track(self, version: int) -> None:
        """Record a read of this cell at `version` with whatever is consuming it."""
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = version

    def read(self) -> Any:
        """Return the value this reader should see, staged write included, and record it."""
        current = _active()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                # Reading back what this action staged answers "what did I just write", not
                # "what is the world", so it is not an observation and carries no precondition.
                self.track(entry.version)
                return entry.value
            if self.address is not None and _RELAXED_READS.get() == 0:
                current.observe(self)
        self.track(self.version)
        return self.value

    def write(self, value: Any) -> None:
        self.value = value
        self.version += 1
        _bump_epoch()

    def touch(self) -> None:
        """Say the held value changed in place: the version moves, the value does not."""
        self.version += 1
        _bump_epoch()

    def restore(self, value: Any, version: int) -> None:
        """Put a cell back exactly as an action found it, version included.

        The version goes back rather than forward: a reader that sampled this cell before the
        action is still valid, and bumping would make it recompute for a value that never
        changed. The epoch still moves, because settled readers must re-check.
        """
        self.value = value
        self.version = version
        _bump_epoch()


def _staged_value(cell: _Cell) -> Any:
    """This action's pending value for `cell`, or `_MISSING` if it has not written one."""
    current = _active()
    if current is None:
        return _MISSING
    entry = current.staged(cell)
    return _MISSING if entry is None else entry.value


class _Consumer(Protocol):
    """Whatever a tracked read reports itself to: a computed, or a resource load."""

    sources: dict[Any, int]


_CONSUMER: ContextVar[_Consumer | None] = ContextVar("squid_reactive_consumer", default=None)

_SETTLING: ContextVar[tuple[Any, ...]] = ContextVar("squid_reactive_settling", default=())
"""The derived nodes currently producing a value on this task, outermost first.

Task-local rather than global: two independent values settled concurrently each copy the
context, so one cannot be mistaken for the other's dependency. A computed and a resource share
one stack because a chain can run through both, and a cycle should be named whole.
"""


def cycle_path(node: Any) -> tuple[str, ...] | None:
    """The ring `node` closes if it is already producing a value on this task, else `None`.

    Reported from the first time `node` appears, so the answer is the ring itself and not the
    run-up to it: naming a caller that merely reached the cycle sends the reader to the wrong
    line. `node` needs a `_label`.
    """
    stack = _SETTLING.get()
    for index, entered in enumerate(stack):
        if entered is node:
            return (*(held._label for held in stack[index:]), node._label)
    return None


@contextmanager
def settling(node: Any) -> Iterator[None]:
    """Mark `node` as producing a value, and refuse to enter a ring twice.

    The check precedes the push, so the error is raised before whatever would otherwise
    deadlock or recurse.
    """
    path = cycle_path(node)
    if path is not None:
        raise ReactiveCycleError(path)
    token = _SETTLING.set((*_SETTLING.get(), node))
    try:
        yield
    finally:
        _SETTLING.reset(token)


_OBSERVING: ContextVar[bool] = ContextVar("squid_reactive_observing_reads", default=False)

_RENDER_OBSERVATION: ContextVar[Observation | None] = ContextVar("squid_reactive_read_observation", default=None)
"""The `Observation` for the render in progress on this task, set only while `rendering()` is.

A separate var from `_CONSUMER`: a computed evaluated mid-render points `_CONSUMER` at its own
node, not the render, so the born-set below needs its own line back to the render itself.
"""


def rendering() -> bool:
    """Whether a render is in progress on this task.

    A render produces a tree from state and must not change the state it is reading, so a
    shared write here would publish halfway through building the thing that reads it.
    """
    return _OBSERVING.get()


def note_born(owner: object) -> None:
    """Record an object whose construction began in the active transactional contexts."""
    if current := _CURRENT.get():
        current.note_born(owner)
    if observation := _RENDER_OBSERVATION.get():
        observation.note_born(owner)


def note_initialized(owner: object) -> None:
    """End an object's construction exemption in the active render observation."""
    if observation := _RENDER_OBSERVATION.get():
        observation.entering_own_render(owner)


_EPOCH = 0
"""Bumped by every write anywhere.

A node settled in the current epoch cannot be stale, so it can skip walking its sources --
which is the whole of a render, where reads are many and writes are none.
"""


def _bump_epoch() -> None:
    global _EPOCH
    _EPOCH += 1


@contextmanager
def untracked() -> Iterator[None]:
    """Read state without subscribing to it.

    For code that reads to decide what to do rather than to derive a value from it. Action
    handlers already run outside any consumer, so this is for a computed that deliberately
    samples something it does not want to recompute for.
    """
    token = _CONSUMER.set(None)
    try:
        yield
    finally:
        _CONSUMER.reset(token)


@contextmanager
def relaxed_read() -> Iterator[None]:
    """Read shared state without adding a commit-time consistency precondition.

    Unlike :func:`untracked`, this does not change reactive dependency tracking.
    """
    token = _RELAXED_READS.set(_RELAXED_READS.get() + 1)
    try:
        yield
    finally:
        _RELAXED_READS.reset(token)


@dataclass(slots=True)
class _Staged:
    """One cell an action has written, holding both halves of the change.

    The overlay is the snapshot. Rolling back is putting `before` back; committing is
    putting `value` in. Neither copies anything, because a state value is immutable.
    """

    owner: ReactiveOwner
    name: str
    cell: _Cell
    before: Any
    before_version: int
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


@dataclass(frozen=True, slots=True)
class CellTarget:
    """The stable runtime identity of one reversible state slot."""

    owner: ReactiveOwner
    name: str
    cell: _Cell

    @property
    def identity(self) -> str:
        address = self.cell.address
        return str(address) if address is not None else f"cell:{id(self.cell)}"


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
            address = patch.target.cell.address
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
    for patch in patches:
        current.require_version(patch.target.cell, patch.expected_version)
    for patch in patches:
        _write(patch.target.owner, patch.target.name, patch.target.cell, patch.value.raw())


def _cell_for(owner: ReactiveOwner, name: str) -> _Cell:
    """Return the cell behind one state slot, empty until something assigns it.

    The cell outlives every value it holds, so a reader that recorded it keeps seeing this
    field even across a restore that puts the field back to unassigned.
    """
    cell = owner.__dict__.get(name)
    if cell is None:
        cell = owner.__dict__[name] = _Cell()
    return cell


def _write(owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
    """Stage a write into the action in flight, or land it now if there is none.

    `join_action` already returns `None` when no transaction is open; this is the same
    signal read directly. A component built during the action is not protected by it, so
    its construction lands immediately too.
    """
    current = _CURRENT.get()
    if current is None and in_aftermath():
        message = "direct reactive mutation in an aftermath hook is forbidden; use aftermath.start_action(...)"
        raise ReactiveWriteError(message)
    if current is not None and not current.writable():
        current.reject_stale(f"{type(owner).__name__}.{name}")
    if current is None or not current.protects(owner):
        cell.write(value)
        owner._state_changed(frozenset((name,)))
        return
    current.stage(owner, name, cell, value)


@dataclass(slots=True)
class _Transaction:
    readonly: bool = False
    closed: bool = False
    """Whether this transaction has finished, successfully or not.

    Set once the commit or the rollback is done, so a task still holding an inherited copy
    finds out rather than staging into an action nobody will publish.
    """
    owner_task: object | None = None
    """The task that opened this transaction, or `None` outside a running loop."""
    writes: dict[_Cell, _Staged] = field(default_factory=dict)
    observed: dict[_Cell, int] = field(default_factory=dict)
    preconditions: dict[_Cell, int] = field(default_factory=dict)
    """Committed values this action read out of addressed cells, first read per cell.

    Only shared cells are recorded, because only they can move under a running action.
    """
    changed: dict[int, ReactiveOwner] = field(default_factory=dict)
    changed_names: dict[int, set[str]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    commit_hooks: list[tuple[object | None, Callable[[ActionCommit, Aftermath], None]]] = field(default_factory=list)
    rollback_hooks: list[tuple[object | None, Callable[[ActionRollback, Aftermath], None]]] = field(
        default_factory=list
    )
    outcome_hooks: list[tuple[object | None, Callable[[ActionOutcome, Aftermath], None]]] = field(default_factory=list)
    context: ActionContext = field(default_factory=ActionContext.create)
    participants: dict[object, ActionParticipant[Any]] = field(default_factory=dict)
    applied: bool = False
    """Whether `publish` has put the staged values into the cells.

    Rollback before this point must not write: the cells were never touched, and a shared
    cell may hold a value someone else committed while this action was staging. Restoring
    `before` over that would revert a write this action never saw.
    """
    published: bool = False
    """Whether the commit reached the point where this action's writes became visible."""
    write_block: str | None = None
    """Why state may not be written right now, while the transaction itself stays open."""
    aborted: bool = False
    cleanup_errors: list[ExceptionSummary] = field(default_factory=list)
    prepared_participants: tuple[tuple[ActionParticipant[Any], Any], ...] = ()
    commit_record: ActionCommit | None = None

    def note_born(self, owner: object) -> None:
        """Record an object created after this transaction began."""
        self.born[id(owner)] = owner

    def writable(self) -> bool:
        """Whether this caller may stage into this transaction at all.

        Prior to every policy question `stage` asks: those are about what the action is
        allowed to do, this is about whether the caller is part of the action.
        """
        return not self.closed and (self.owner_task is None or self.owner_task is _current_task())

    def reject_stale(self, what: str) -> None:
        """Refuse a write from a caller with no standing to make it, saying which mistake."""
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

    def observe(self, cell: _Cell) -> None:
        """Note that this action looked at a shared cell's committed value.

        The first read is the one kept: a later write does not clear it, because the action
        has already branched on what it saw.
        """
        if cell not in self.observed:
            self.observed[cell] = cell.version

    def check_preconditions(self) -> None:
        """Reject a publishing action if any strong input or explicit precondition moved.

        Blind writes remain last-commit-wins. Every strong read guards a transaction that
        publishes either cell or participant state, including reads of cells it did not write.
        """
        if not (self.writes or self.participants):
            return
        required = dict(self.observed)
        required.update(self.preconditions)
        for cell, version in required.items():
            if cell.version == version:
                continue
            detail = ConflictDetail(self.target_id(cell), version, cell.version)
            message = (
                f"{detail.target_id} changed while this action was running: expected version "
                f"{version}, found {cell.version}. Nothing was published."
            )
            raise ReactiveConflictError(detail, message)

    def target_id(self, cell: _Cell) -> str:
        if cell.address is None:
            return f"cell:{id(cell)}"
        if hasattr(cell.address, "owner") and hasattr(cell.address, "name"):
            return f"{cell.address.owner!r}.{cell.address.name}"
        return str(cell.address)

    def require_version(self, cell: _Cell, version: int) -> None:
        existing = self.preconditions.get(cell)
        if existing is not None and existing != version:
            detail = ConflictDetail(self.target_id(cell), existing, version)
            raise ReactiveConflictError(detail, "incompatible inverse preconditions")
        self.preconditions[cell] = version

    def stage(self, owner: ReactiveOwner, name: str, cell: _Cell, value: Any) -> None:
        """Hold a write until this action commits, so nothing else can read it meanwhile."""
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot mutate component state"
            raise ReactiveWriteError(message)
        entry = self.writes.get(cell)
        if entry is None:
            self.writes[cell] = _Staged(owner, name, cell, cell.value, cell.version, value, cell.version + 1)
        else:
            entry.value = value
            entry.version += 1
        self.changed[id(owner)] = owner
        self.changed_names.setdefault(id(owner), set()).add(name)
        # The staged version is what a read inside this action reports, so a computed that
        # sampled it has to be told the world moved even though nothing is published yet.
        _bump_epoch()

    def publish(self) -> None:
        """Make this action's writes visible. The staged version becomes the cell's, so a
        reader that already sampled it inside the action stays valid across the commit."""
        for entry in self.writes.values():
            entry.cell.value = entry.value
            entry.cell.version = entry.version
        self.applied = True
        _bump_epoch()

    def restore(self) -> None:
        """Put every written cell back where the action found it.

        Only if the values ever left the overlay. An action that fails before publication --
        every handler failure, and every rejected commit precondition -- never touched a cell,
        so there is nothing to put back and writing anyway would clobber whoever did.
        """
        for entry in reversed(tuple(self.writes.values())):
            if self.applied:
                entry.cell.restore(entry.before, entry.before_version)
            entry.owner._state_rolled_back()
        if self.writes:
            # Even when nothing was written back: a computed that read a staged version is
            # settled against a version that has just stopped existing, and only the epoch
            # tells it to look again.
            _bump_epoch()

    def enlist[ParticipantT: ActionParticipant[Any]](
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
        return CellPatchSet(
            tuple(
                CellPatch(
                    CellTarget(entry.owner, entry.name, entry.cell),
                    SlotValue.from_raw(entry.before),
                    SlotValue.from_raw(entry.value),
                    entry.before_version,
                    entry.version,
                )
                for entry in self.writes.values()
            )
        )

    def reads(self) -> tuple[ObservedRead, ...]:
        return tuple(ObservedRead(self.target_id(cell), version) for cell, version in self.observed.items())

    def commit(self) -> ActionCommit:
        """Prepare everything, publish synchronously, and return the immutable commit."""
        self.check_preconditions()
        view = _FrozenTransactionView(self)
        prepared: list[tuple[ActionParticipant[Any], Any]] = []
        try:
            for participant in self.participants.values():
                prepared.append((participant, participant.prepare(view)))  # noqa: PERF401
        except BaseException as error:
            self.abort(error, prepared)
            raise
        changes = tuple(
            change for participant, value in prepared if (change := participant.describe_change(value)) is not None
        )
        commit = ActionCommit(
            self.context,
            next_commit_sequence(),
            datetime.now(UTC),
            elapsed(self.context),
            self.reads(),
            self.patches(),
            changes,
        )
        self.commit_record = commit
        self.prepared_participants = tuple(prepared)
        self.publish()
        # Cell installation begins the infallible publication phase. An adapter that raises
        # from apply has violated the participant contract; rolling cells back could not undo
        # a participant that applied only partly, so preserve the committed diagnostic truth.
        self.published = True
        try:
            for participant, value in prepared:
                participant.apply(value)
        except BaseException:
            _log.critical("an infallible transaction participant apply failed", exc_info=True)
            raise

        return commit

    def finalize_commit(self) -> None:
        """Notify reactive consumers after the commit gate, isolating aftermath failures."""
        for owner in self.changed.values():
            try:
                owner._state_changed(frozenset(self.changed_names[id(owner)]))
            except Exception:
                _log.exception("a reactive owner failed to process its committed state")
        for participant, value in self.prepared_participants:
            try:
                participant.finalize(value)
            except Exception:
                _log.exception("a transaction participant failed to finalize")

    def abort(
        self,
        cause: BaseException,
        prepared: Sequence[tuple[ActionParticipant[Any], Any]] = (),
    ) -> tuple[ExceptionSummary, ...]:
        if self.aborted:
            return tuple(self.cleanup_errors)
        self.aborted = True
        values = {id(participant): value for participant, value in prepared}
        failures: list[ExceptionSummary] = []
        for participant in reversed(tuple(self.participants.values())):
            try:
                participant.abort(values.get(id(participant)), cause)
            except Exception as error:
                summary = ExceptionSummary.capture(error)
                failures.append(summary)
                self.cleanup_errors.append(summary)
                _log.exception("a transaction participant failed to abort")
        self.restore()
        return tuple(failures)


@dataclass(frozen=True, slots=True)
class _FrozenTransactionView:
    """The immutable overlay exposed only during participant preparation."""

    transaction: _Transaction

    @property
    def context(self) -> ActionContext:
        return self.transaction.context

    def read_staged(self, target: CellTarget) -> SlotValue:
        staged = self.transaction.staged(target.cell)
        return SlotValue.from_raw(target.cell.value if staged is None else staged.value)

    def read_committed(self, target: CellTarget) -> SlotValue:
        return SlotValue.from_raw(target.cell.value)


def report_undeclared_write(owner: object, name: str) -> None:
    """Reject a transaction-time write to an attribute that is not declared state.

    Raised before the write lands, so the transaction rolls back whole. The alternative --
    letting it land and logging that it is uncovered -- produced exactly the corruption it
    described: the attribute stays written while everything around it is restored.

    A component built during the action is exempt; assigning to something the action created
    is construction, not mutation.
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
    if during_commit:
        return RollbackReason.PARTICIPANT_PREPARE_FAILED
    return RollbackReason.HANDLER_EXCEPTION


def _notify_hooks(
    outcome: ActionOutcome,
    hooks: Sequence[tuple[object | None, Callable[[Any, Aftermath], None]]],
) -> None:
    aftermath = Aftermath(outcome)
    with aftermath_callback():
        for _, callback in hooks:
            try:
                result = callback(outcome, aftermath)
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    message = "action aftermath hooks must be synchronous; start an operation instead"
                    raise TypeError(message)  # noqa: TRY301
            except Exception:
                _log.exception("an action aftermath hook failed")


def _emit_commit(current: _Transaction, commit: ActionCommit) -> None:
    emit_outcome(commit)
    _notify_hooks(commit, current.commit_hooks)
    _notify_hooks(commit, current.outcome_hooks)


def _emit_rollback(current: _Transaction, error: BaseException, *, during_commit: bool) -> ActionRollback:
    cleanup_errors = current.abort(error)
    rollback = ActionRollback(
        current.context,
        datetime.now(UTC),
        elapsed(current.context),
        _rollback_reason(error, during_commit=during_commit),
        current.reads(),
        error.detail if isinstance(error, ReactiveConflictError) else None,
        ExceptionSummary.capture(error),
        ChangeSummary(len(current.writes), len(current.participants)),
        cleanup_errors,
    )
    emit_outcome(rollback)
    _notify_hooks(rollback, current.rollback_hooks)
    _notify_hooks(rollback, current.outcome_hooks)
    return rollback


@contextmanager
def transaction(*, action_context: ActionContext | None = None) -> Iterator[None]:
    """Commit one identified action atomically or emit one immutable rollback outcome."""
    outer = _CURRENT.get()
    if outer is not None:
        # Checked at the `with`, not at the first write inside it, so a task that inherited
        # someone else's transaction is told where it went wrong rather than where it showed.
        if not outer.writable():
            outer.reject_stale("a nested transaction")
        yield
        return
    context = action_context or current_action() or ActionContext.create()
    current = _Transaction(owner_task=_current_task(), context=context)
    with action_scope(context):
        token = _CURRENT.set(current)
        try:
            yield
        except BaseException as error:
            _CURRENT.reset(token)
            current.closed = True
            _emit_rollback(current, error, during_commit=False)
            raise
        _CURRENT.reset(token)
        try:
            with _COMMIT_GATE:
                commit = current.commit()
        except BaseException as error:
            current.closed = True
            if current.published:
                # Publication is the commit point. An apply failure is an adapter integrity
                # defect, not a user rollback; retain the commit truth and fail loudly.
                assert current.commit_record is not None
                integrity_commit = replace(
                    current.commit_record,
                    tags=frozenset({RollbackReason.FRAMEWORK_INTEGRITY_FAILURE.value}),
                )
                emit_outcome(integrity_commit)
            else:
                _emit_rollback(current, error, during_commit=True)
            raise
        current.finalize_commit()
        current.closed = True
        _emit_commit(current, commit)


@contextmanager
def fresh_action_transaction(*, action_context: ActionContext) -> Iterator[None]:
    """Start a distinct causal transaction while an empty admitting transaction is open.

    Mounted undo/redo handlers are admitted inside the dispatch transaction. Suspending that
    empty envelope lets the history operation remain a real action with its own identity. An
    outer transaction that already staged work is rejected because committing the inner action
    could not then be rolled back with the outer one.
    """
    outer = _CURRENT.get()
    if outer is not None and (outer.writes or outer.participants or outer.preconditions):
        message = "a fresh action cannot start after the admitting transaction staged changes"
        raise RuntimeError(message)
    token = _CURRENT.set(None)
    try:
        with transaction(action_context=action_context):
            yield
    finally:
        _CURRENT.reset(token)


@contextmanager
def batch() -> Iterator[None]:
    """Coalesce related state writes into one invalidation per component."""
    with transaction():
        yield


@contextmanager
def readonly_transaction() -> Iterator[None]:
    """Run an identified read-only action and reject any state mutation."""
    if _CURRENT.get() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    context = current_action() or ActionContext.create("read-only action")
    current = _Transaction(readonly=True, owner_task=_current_task(), context=context)
    with action_scope(context):
        token = _CURRENT.set(current)
        try:
            yield
        except BaseException as error:
            _CURRENT.reset(token)
            current.closed = True
            _emit_rollback(current, error, during_commit=False)
            raise
        _CURRENT.reset(token)
        try:
            with _COMMIT_GATE:
                commit = current.commit()
        except BaseException as error:
            current.closed = True
            _emit_rollback(current, error, during_commit=True)
            raise
        current.finalize_commit()
        current.closed = True
        _emit_commit(current, commit)


def on_action_commit(callback: Callable[[ActionCommit, Aftermath], None], *, key: object | None = None) -> None:
    """Run a failure-isolated synchronous callback after definitive commit."""
    _add_action_hook("commit", callback, key=key)


def on_action_rollback(callback: Callable[[ActionRollback, Aftermath], None], *, key: object | None = None) -> None:
    """Run a failure-isolated callback after staged state is dead."""
    _add_action_hook("rollback", callback, key=key)


def on_action_outcome(callback: Callable[[ActionOutcome, Aftermath], None], *, key: object | None = None) -> None:
    """Run a failure-isolated callback after either terminal outcome."""
    _add_action_hook("outcome", callback, key=key)


def _add_action_hook(kind: str, callback: Callable[..., None], *, key: object | None) -> None:
    current = _CURRENT.get()
    if current is None:
        message = "commit hooks are only available inside an action's transaction"
        raise RuntimeError(message)
    if not current.writable():
        current.reject_stale("a commit hook")
    if current.readonly:
        message = "a read-only action changed nothing, so it has nothing to record"
        raise ReactiveWriteError(message)
    if key is not None and has_action_hook(key):
        message = f"{key!r} already registered a commit hook for this action"
        raise RuntimeError(message)
    hooks = {
        "commit": current.commit_hooks,
        "rollback": current.rollback_hooks,
        "outcome": current.outcome_hooks,
    }[kind]
    hooks.append((key, callback))


def join_action[ParticipantT: ActionParticipant[Any]](
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


def action_participant(key: object) -> ActionParticipant[Any] | None:
    """`key`'s participant in the action in flight, without enlisting one.

    The read half of :func:`join_action`, for a subsystem that has to answer "what did this
    action stage" on a path where staging itself would be wrong -- a read, or a render.
    """
    current = _CURRENT.get()
    return None if current is None else current.participants.get(key)


def has_action_hook(key: object) -> bool:
    """Whether `key` already registered any outcome hook for the action in flight."""
    current = _CURRENT.get()
    if current is None:
        return False
    return any(
        registered is key
        for hooks in (current.commit_hooks, current.rollback_hooks, current.outcome_hooks)
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


class _State:
    def __init__(
        self,
        default: Any = _MISSING,
        *,
        factory: Callable[[], Any] | None = None,
        persist: bool | None = None,
        opaque: bool = False,
    ) -> None:
        if default is not _MISSING and factory is not None:
            message = "state accepts either a default or a factory, not both"
            raise TypeError(message)
        if opaque and persist:
            message = "opaque state cannot be persisted; it is not serializable by assumption"
            raise TypeError(message)
        self._default = default
        self._factory = factory
        self._name = ""
        self.public_name = ""
        self.opaque = opaque
        self.persist = (not opaque) if persist is None else persist
        self.persist_declared = persist is not None
        """Whether the author asked for persistence, as opposed to taking the default.

        An owner that cannot persist anything needs to tell the difference, so it can refuse
        `persist=True` without refusing every field that merely defaulted to it.
        """

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__state_{name}"
        self.public_name = name

    @property
    def has_initial(self) -> bool:
        return self._default is not _MISSING or self._factory is not None

    def _initial(self, instance: ReactiveOwner) -> Any:
        if self._factory is None:
            # No copy: a state value is replaced, never mutated, so instances may share it.
            return self._default
        return self._factory()

    def address(self, instance: ReactiveOwner) -> Any:
        """What a write to this field publishes under, or `None` if nobody outside can see it.

        Asked of the *instance*, not declared at the field: whether state is private to one
        owner or shared with every mount holding it is what kind of object holds it, and the
        object already knows. A namespace answers with an address; a component has no hook and
        so has no address.
        """
        binding = getattr(instance, "_state_binding", None)
        return None if binding is None else binding(self.public_name)

    def cell(self, instance: ReactiveOwner) -> _Cell:
        """Return this field's storage on `instance`, empty until something assigns it."""
        cell = instance.__dict__.get(self._name)
        if cell is None:
            cell = instance.__dict__[self._name] = _Cell(address=self.address(instance))
        return cell

    def is_set(self, instance: ReactiveOwner) -> bool:
        """Whether this field currently holds a value of its own rather than its default."""
        cell = instance.__dict__.get(self._name)
        return cell is not None and (cell.value is not _MISSING or _staged_value(cell) is not _MISSING)

    def mutated(self, instance: ReactiveOwner) -> None:
        """Note that the held value changed in place: the version moves, the value does not."""
        self.cell(instance).touch()

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cell = instance.__dict__.get(self._name)
        if cell is not None and (cell.value is not _MISSING or _staged_value(cell) is not _MISSING):
            return cell.read()
        if not self.has_initial:
            message = f"{type(instance).__name__}.{self.public_name} was never assigned"
            raise AttributeError(message)
        # Materializing a default is not a write: no version bump, no invalidation.
        cell = self.cell(instance)
        cell.value = self._initial(instance)
        return cell.read()

    def __set__(self, instance: ReactiveOwner, value: Any) -> None:
        if rendering():
            if self.address(instance) is not None:
                # Shared state: visible to other mounts mid-render, a correctness problem
                # beyond this tree, so no exemption applies -- not even to a fresh instance.
                message = (
                    f"{instance!r}.{self.public_name} was written while a render was reading it. "
                    f"A render turns state into a tree and may not change the state it is "
                    f"reading; write shared state from an action handler."
                )
                raise ReactiveWriteError(message)
            observation = _RENDER_OBSERVATION.get()
            if observation is None or not observation.exempts(instance):
                # Component state: nothing publishes it beyond this tree, but a torn read
                # (write, then read back) still draws a tree no single state ever produced,
                # silently and finally -- the invalidation the write would schedule never fires.
                message = (
                    f"{type(instance).__name__}.{self.public_name} was written while a render "
                    f"was reading it. A render must produce one tree from the state it reads, "
                    f"so a write here tears: use state(factory=...) for a lazy-initialized "
                    f"default, computed for a value derived from state, a resource or "
                    f"on_load for something fetched, and on_mount to react to having been drawn."
                )
                raise ReactiveWriteError(message)
        cell = instance.__dict__.get(self._name)
        if cell is not None:
            held = _staged_value(cell)
            if held is _MISSING:
                held = cell.value
            # Opaque fields compare by identity: their values are collaborators, and `==` on
            # one is the author's code, not a cheap settled-value check.
            if held is value if self.opaque else _equal(held, value):
                return
        _write(instance, self._name, self.cell(instance), value)


@overload
def state[KeyT, ValueT](
    default: dict[KeyT, ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> Mapping[KeyT, ValueT]: ...


@overload
def state[ValueT](
    default: list[ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> Sequence[ValueT]: ...


@overload
def state[ValueT](
    default: set[ValueT],
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> AbstractSet[ValueT]: ...


@overload
def state[ValueT](
    default: ValueT,
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> ValueT: ...


@overload
def state[KeyT, ValueT](
    *,
    factory: Callable[[], dict[KeyT, ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> Mapping[KeyT, ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], list[ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> Sequence[ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], set[ValueT]],
    persist: bool | None = None,
    opaque: bool = False,
) -> AbstractSet[ValueT]: ...


@overload
def state[ValueT](
    *,
    factory: Callable[[], ValueT],
    persist: bool | None = None,
    opaque: bool = False,
) -> ValueT: ...


@overload
def state(*, persist: bool | None = None, opaque: bool = False) -> Any: ...


def state(
    default: Any = _MISSING,
    *,
    factory: Callable[[], Any] | None = None,
    persist: bool | None = None,
    opaque: bool = False,
) -> Any:
    """Declare observed component state.

    Pass a default or a factory for a field the class owns, or neither for one ``__init__``
    assigns. A value is replaced, never mutated in place: an in-place change moves no version.
    The type checker holds that line -- a ``dict``, ``list`` or ``set`` default declares the
    field as ``Mapping``, ``Sequence`` or ``AbstractSet``, so a concrete annotation and every
    mutating method are type errors. The stored value is the one assigned. ``opaque=True``
    declares a collaborator the component holds -- a service, a guild, a session -- which
    settles on identity rather than ``==`` and is never persisted.
    """
    return _State(default, factory=factory, persist=persist, opaque=opaque)


@dataclass(frozen=True, slots=True)
class CellReport:
    """One state field as devtools sees it."""

    identity: int
    """`id()` of the cell, so a computed's sources can be resolved back to a name."""
    version: int
    assigned: bool
    """Whether the field holds a value of its own rather than its declared default."""
    opaque: bool


@dataclass(frozen=True, slots=True)
class ComputedReport:
    """One computed as devtools sees it, including what it currently depends on."""

    identity: int
    evaluated: bool
    """False until something reads it. A computed nobody renders is never evaluated."""
    version: int
    sources: tuple[int, ...]
    """Identities of the cells and computeds its last run read, in read order."""


def inspect_cells(owner: ReactiveOwner) -> dict[str, CellReport]:
    """Report every declared state field on `owner`, by public name."""
    reports: dict[str, CellReport] = {}
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                reports[name] = CellReport(id(cell), cell.settle(), descriptor.is_set(owner), descriptor.opaque)
    return reports


def inspect_computed(owner: ReactiveOwner) -> dict[str, ComputedReport]:
    """Report every computed on `owner`, without evaluating one that has never run."""
    reports: dict[str, ComputedReport] = {}
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _Computed):
                node = descriptor.node(owner)
                reports[name] = ComputedReport(
                    id(node), node.evaluated, node.version, tuple(id(source) for source in node.sources)
                )
    return reports


def declared_cells(owner: ReactiveOwner) -> dict[Any, int]:
    """Every declared state cell on `owner`, at the version it holds now.

    The presumed dependency set for something that has not yet said what it reads -- a
    resource holding a value that was installed rather than loaded. Over-subscribing is the
    safe direction, and the first real run replaces the presumption with the truth.
    """
    presumed: dict[Any, int] = {}
    for klass in type(owner).__mro__:
        for descriptor in vars(klass).values():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                presumed[cell] = cell.settle()
    return presumed


def _state_fields(owner: ReactiveOwner) -> dict[str, _State]:
    fields: dict[str, _State] = {}
    for cls in reversed(type(owner).__mro__):
        fields.update(
            (name, descriptor)
            for name, descriptor in vars(cls).items()
            if isinstance(descriptor, _State) and descriptor.persist
        )
    return fields


def export_state(owner: ReactiveOwner) -> dict[str, Any]:
    """Return the value of every persistent state descriptor that currently has one."""
    return {
        name: getattr(owner, name)
        for name, descriptor in _state_fields(owner).items()
        if descriptor.has_initial or descriptor.is_set(owner)
    }


def restore_state(owner: ReactiveOwner, values: Mapping[str, Any]) -> None:
    """Restore declared persistent state, rejecting stale or misspelled field names."""
    fields = _state_fields(owner)
    unknown = set(values) - set(fields)
    if unknown:
        message = f"snapshot has unknown state fields: {', '.join(sorted(unknown))}"
        raise ValueError(message)
    with transaction():
        for name, value in values.items():
            setattr(owner, name, _frozen(value))


class _Derived:
    """One computed's per-instance node: what it read last, and what it returned.

    Sources are held by the node, never the other way round. A component here is per-message
    and constantly dropped, and a source holding its readers would keep every one of them
    alive; a version comparison at read time answers the same question with no back-edge.
    """

    __slots__ = ("_epoch", "_function", "_label", "_settled", "owner", "sources", "value", "version")

    def __init__(self, function: Callable[[Any], Any], owner: ReactiveOwner, label: str) -> None:
        self._function = function
        self._label = label
        self.owner = owner
        self.sources: dict[Any, int] = {}
        self.value: Any = None
        self.version = 0
        self._settled = False
        self._epoch = -1

    def settle(self) -> int:
        """Return this node's current version, recomputing only if a source moved."""
        if self._epoch == _EPOCH:
            return self.version
        if self._settled and all(source.settle() == seen for source, seen in self.sources.items()):
            self._epoch = _EPOCH
            return self.version
        # Cleared before the run, not after: a body that raises must leave the node asking to
        # be recomputed rather than holding a value nothing verified.
        self._settled = False
        self._epoch = -1
        self.sources = {}
        token = _CONSUMER.set(self)
        try:
            with settling(self):
                value = self._function(self.owner)
        finally:
            _CONSUMER.reset(token)
        self._settled = True
        # Recomputing is not a write, so the epoch stays put: nothing downstream can be stale
        # from it, and bumping would make every settled node in the process walk its sources.
        self._epoch = _EPOCH
        if not _equal(self.value, value):
            self.value = value
            self.version += 1
        return self.version

    @property
    def evaluated(self) -> bool:
        """Whether this node has ever produced a value. Reading it is what does that."""
        return self._settled

    def read(self) -> Any:
        self.settle()
        consumer = _CONSUMER.get()
        if consumer is not None:
            consumer.sources[self] = self.version
        return self.value


class _Computed:
    def __init__(self, function: Callable[[Any], Any]) -> None:
        self._function = function
        self.public_name = function.__name__
        self._name = f"__computed_{function.__name__}"
        self._label = function.__name__

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__computed_{name}"
        self._label = f"{owner.__name__}.{name}"

    def node(self, instance: ReactiveOwner) -> _Derived:
        """Return this computed's node on `instance`, unevaluated until something reads it."""
        node = instance.__dict__.get(self._name)
        if node is None:
            node = instance.__dict__[self._name] = _Derived(self._function, instance, self._label)
        return node

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return self.node(instance).read()


def computed[ValueT](function: Callable[[Any], ValueT]) -> ValueT:
    """Derive a value from the state its body reads, recomputed when that state moves.

    The dependency set is what the last run actually read, so a conditional dependency is
    exact rather than over-declared, and nothing has to be named twice. A computed nobody
    reads is never evaluated, and one that raises does so where its value is used.
    """
    return _Computed(function)  # pyrefly: ignore[bad-return]


@dataclass(slots=True)
class Observation:
    """What one run read, so its shared cells can be turned into bus addresses.

    A plain :class:`_Consumer`: the same tracked read a computed records is what a render
    records, and the context the read happens in is the only difference between them. For a
    shared cell that is the whole story -- there is no separate ``watch()`` to forget to
    call. :func:`squid_reactive.watch` exists only for a named topic, which has no value to read.
    """

    sources: dict[Any, int] = field(default_factory=dict)
    born: dict[int, object] = field(default_factory=dict)
    """Components constructed during this render, by id, while their own render() has not yet
    run. Assigning declared state in __init__ is construction, not the mutation the write guard
    exists to catch -- the same rule `_Transaction.protects` applies to an action.

    Held by strong reference for the same reason as `_Transaction.born`: an id must not be
    recycled onto something else while this render is still using it as a key.
    """

    def note_born(self, owner: object) -> None:
        """Exempt `owner`'s writes from the render guard until its own render() runs."""
        self.born[id(owner)] = owner

    def entering_own_render(self, owner: object) -> None:
        """`owner`'s own render() is starting: end its construction exemption.

        Scoped to this one instance, not the whole render -- otherwise a child could tear its
        own tree and be excused for it because some ancestor built it a moment earlier.
        """
        self.born.pop(id(owner), None)

    def exempts(self, owner: object) -> bool:
        """Whether `owner` is still within the construction window this render excuses."""
        return id(owner) in self.born

    def addresses(self) -> tuple[Any, ...]:
        """Every addressed cell this run reached, deduplicated, in read order.

        Anything with sources of its own is walked rather than re-run -- a cached computed, a
        settled resource. It did not read its sources again, but a reader that used its value
        still depends on every one of them, so a topic watched two loaders down is still this
        render's dependency. One that carries an address of its own contributes that too.
        """
        found: list[Any] = []
        seen: set[int] = set()

        def walk(sources: dict[Any, int]) -> None:
            for source in sources:
                identity = id(source)
                if identity in seen:
                    continue
                seen.add(identity)
                if isinstance(source, _Cell):
                    if source.address is not None:
                        found.append(source.address)
                elif (nested := getattr(source, "sources", None)) is not None:
                    # An addressed source is followed *and* walked. A resource on a namespace
                    # publishes its own address when it reloads, but it can also be re-pended
                    # by a cell its loader read, and a reader depends on both routes.
                    address = getattr(source, "address", None)
                    if address is not None:
                        found.append(address)
                    walk(nested)

        walk(self.sources)
        return tuple(found)


@contextmanager
def observe_render() -> Iterator[Observation]:
    """Collect the shared cells one component render read, and forbid writing them."""
    observation = Observation()
    consumer = _CONSUMER.set(observation)
    observing = _OBSERVING.set(True)
    render = _RENDER_OBSERVATION.set(observation)
    try:
        yield observation
    finally:
        _RENDER_OBSERVATION.reset(render)
        _OBSERVING.reset(observing)
        _CONSUMER.reset(consumer)


@contextmanager
def observe_reads() -> Iterator[Observation]:
    """Collect addressed reactive sources read by one pure projection."""
    with observe_render() as observation:
        yield observation


def addresses(read: Callable[[], object]) -> tuple[Any, ...]:
    """The bus addresses of the shared cells `read` touches, for following them by hand.

    ``addresses(lambda: preferences.theme)`` names a cell the way the rest of the package
    does -- by reading it -- so the thunk is ordinary typed code with no class name repeated
    and no string to drift. Reading a computed yields the shared cells behind it. Raises if
    the thunk reaches no shared cell at all, so a typo cannot quietly follow nothing.
    """
    with observe_render() as observation:
        read()
    found = observation.addresses()
    if not found:
        message = (
            "addresses() read no shared state: the callable must read at least one state() "
            "off a namespace, directly or through a computed. Component state has no address."
        )
        raise ValueError(message)
    return found


def _checked_init(
    original: Callable[..., None],
    required: tuple[tuple[str, _State], ...],
) -> Callable[..., None]:
    """Wrap an initializer so required reactive state is assigned before it returns."""

    @functools.wraps(original)
    def __init__(self: Reactive, *args: Any, **kwargs: Any) -> None:
        try:
            original(self, *args, **kwargs)
            if type(self).__init__ is not __init__:
                return
            missing = sorted(name for name, descriptor in required if not descriptor.is_set(self))
            if missing:
                message = f"{type(self).__name__}.__init__ left declared state unassigned: {', '.join(missing)}"
                raise TypeError(message)
        finally:
            if type(self).__init__ is __init__:
                note_initialized(self)

    return __init__


class Reactive:
    """Reusable owner for transactional state and computed values.

    Subclasses normally override :meth:`on_state_commit` to invalidate whatever projection
    reads them. The base owns descriptor discovery, construction exemptions, rollback-safe
    attribute protection, required fields, and opaque collaborator mutation.
    """

    _state_names: ClassVar[frozenset[str]] = frozenset()
    _state_descriptors: ClassVar[dict[str, _State]] = {}
    _opaque_state: ClassVar[tuple[tuple[str, _State], ...]] = ()
    _computed_descriptors: ClassVar[dict[str, _Computed]] = {}
    _reactive_internal_attributes: ClassVar[frozenset[str]] = frozenset()
    _reactive_require_state: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = {
            name: descriptor
            for klass in reversed(cls.__mro__)
            for name, descriptor in vars(klass).items()
            if isinstance(descriptor, _State)
        }
        cls._state_names = frozenset(declared)
        cls._state_descriptors = declared
        cls._opaque_state = tuple((name, descriptor) for name, descriptor in declared.items() if descriptor.opaque)
        cls._computed_descriptors = {
            name: descriptor
            for klass in reversed(cls.__mro__)
            for name, descriptor in vars(klass).items()
            if isinstance(descriptor, _Computed)
        }
        required = tuple((name, descriptor) for name, descriptor in declared.items() if not descriptor.has_initial)
        cls.__init__ = _checked_init(cls.__init__, required if cls._reactive_require_state else ())

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        instance = super().__new__(cls)
        note_born(instance)
        return instance

    def __setattr__(self, name: str, value: Any) -> None:
        if (
            _active() is not None
            and name not in type(self)._reactive_internal_attributes
            and name not in type(self)._state_names
        ):
            report_undeclared_write(self, name)
        object.__setattr__(self, name, value)

    def _state_changed(self, names: frozenset[str]) -> None:
        self.on_state_commit(names)

    def _state_rolled_back(self) -> None:
        self.on_state_rollback()

    def on_state_commit(self, names: frozenset[str]) -> None:
        """React after the named state fields publish together."""

    def on_state_rollback(self) -> None:
        """React after an attempted transaction restores this owner."""

    def invalidate(self) -> None:
        """Invalidate projections owned by this object, if it has any."""

    def mutated(self, collaborator: object) -> None:
        """Signal an in-place change to the object held by one opaque state field."""
        with untracked():
            holders = [
                (name, descriptor)
                for name, descriptor in type(self)._opaque_state
                if descriptor.is_set(self) and descriptor.__get__(self) is collaborator
            ]
        if not holders:
            message = f"no opaque state on {type(self).__name__} holds {collaborator!r}"
            raise TypeError(message)
        if len(holders) > 1:
            names = ", ".join(name for name, _ in holders)
            message = f"{type(self).__name__} holds {collaborator!r} in more than one field ({names})"
            raise TypeError(message)
        _, descriptor = holders[0]
        descriptor.mutated(self)
        self._state_changed(frozenset((descriptor._name,)))

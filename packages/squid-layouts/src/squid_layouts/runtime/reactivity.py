"""Transactional reactive state for component trees.

A state field stores an immutable value in a :class:`_Cell`, next to the version that dates
it. Writing replaces the value; nothing is mutated in place. That is what makes a snapshot a
reference rather than a deep copy, and rolling an action back putting the old reference back.

Reads are tracked. A computed records the cells it read and the version each held, and asked
for its value it recomputes only if one of those versions has moved. Nothing is pushed: every
reference points from reader to source, which is what lets a per-message component be
collected while the state it read lives on.
"""

import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Protocol, overload

_log = logging.getLogger(__name__)

_MISSING = object()
"""A cell that has never been assigned. Distinct from ``None``, which is a real value."""


class ReactiveOwner(Protocol):
    __dict__: dict[str, Any]

    def _state_changed(self, names: frozenset[str]) -> None: ...

    def _state_rolled_back(self) -> None: ...


class ActionParticipant(Protocol):
    """A subsystem that publishes its own writes when the action in flight commits.

    The split is the whole point. Everything that can fail happens in `prepare`, before
    any participant has made anything visible, so the transaction can still roll the
    action back as though it never ran. Register with :func:`join_action`.
    """

    def prepare(self) -> None:
        """Validate the staged writes without publishing any of them.

        Raise to abort the action: every participant is aborted, component state is
        restored, and the error reaches whoever called the handler.
        """

    def apply(self) -> None:
        """Publish the prepared writes. Synchronous, and past the point of failure."""

    def abort(self) -> None:
        """Discard the staged writes. Called once, on rollback or a failed prepare."""

    def finalize(self) -> None:
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


class SharedStateConflictError(RuntimeError):
    """An action read a shared cell, wrote it, and someone else moved it in between.

    Nothing was published: the action rolls back whole and travels the ordinary failed-handler
    path. A handler cannot catch this, because the check runs when its transaction exits.
    """


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


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_layouts_transaction", default=None)


class _Cell:
    """One state field's storage: an immutable value, and the version that dates it.

    `address` is what an addressed cell publishes under: a ``CellAddress`` for a shared cell
    from :mod:`squid_layouts.runtime.shared`, or a ``Topic`` for the valueless cell behind
    ``sl.watch()``. A component's cell has none, and the two behaviours an address adds --
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
        current = _CURRENT.get()
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
        current = _CURRENT.get()
        if current is not None:
            entry = current.staged(self)
            if entry is not None:
                # Reading back what this action staged answers "what did I just write", not
                # "what is the world", so it is not an observation and carries no precondition.
                self.track(entry.version)
                return entry.value
            if self.address is not None:
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
    current = _CURRENT.get()
    if current is None:
        return _MISSING
    entry = current.staged(cell)
    return _MISSING if entry is None else entry.value


class _Consumer(Protocol):
    """Whatever a tracked read reports itself to: a computed, or a resource load."""

    sources: dict[Any, int]


_CONSUMER: ContextVar[_Consumer | None] = ContextVar("squid_layouts_consumer", default=None)

_SETTLING: ContextVar[tuple[Any, ...]] = ContextVar("squid_layouts_settling", default=())
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


_OBSERVING: ContextVar[bool] = ContextVar("squid_layouts_observing_render", default=False)

_RENDER_OBSERVATION: ContextVar[Observation | None] = ContextVar("squid_layouts_render_observation", default=None)
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
class StateChange:
    """One declared attribute as an action found it and as it left it."""

    owner: ReactiveOwner
    name: str
    existed_before: bool
    before: Any
    existed_after: bool
    after: Any


@dataclass(frozen=True, slots=True)
class StateDelta:
    """Every state write one action made, in both directions.

    Restoring goes through the ambient transaction rather than around it, so an undo that
    fails after this point rolls the restore back with everything else it did.
    """

    changes: tuple[StateChange, ...] = ()

    def addresses(self) -> tuple[Any, ...]:
        """Every shared cell address this action wrote, deduplicated, in write order.

        What a frontend needs to answer "did this action change anything I am looking at",
        which is a question about its own commit rather than about the bus. A component's
        cell has no address and does not appear.
        """
        found: list[Any] = []
        seen: set[int] = set()
        for change in self.changes:
            address = _cell_for(change.owner, change.name).address
            if address is None or id(address) in seen:
                continue
            seen.add(id(address))
            found.append(address)
        return tuple(found)

    def restore_before(self) -> None:
        """Return every attribute to the value the action found."""
        self._apply(before=True)

    def restore_after(self) -> None:
        """Return every attribute to the value the action left."""
        self._apply(before=False)

    def _apply(self, *, before: bool) -> None:
        ordered = tuple(reversed(self.changes)) if before else self.changes
        for change in ordered:
            existed = change.existed_before if before else change.existed_after
            value = (change.before if before else change.after) if existed else _MISSING
            # Nothing is copied on the way out either. A delta outlives its action and may be
            # replayed, and an immutable value is safe to hand back as many times as asked.
            _write(change.owner, change.name, _cell_for(change.owner, change.name), value)


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
    if current is None or not current.protects(owner):
        cell.write(value)
        owner._state_changed(frozenset((name,)))
        return
    current.stage(owner, name, cell, value)


@dataclass(slots=True)
class _Transaction:
    readonly: bool = False
    writes: dict[_Cell, _Staged] = field(default_factory=dict)
    observed: dict[_Cell, Any] = field(default_factory=dict)
    """Committed values this action read out of addressed cells, first read per cell.

    Only shared cells are recorded, because only they can move under a running action.
    """
    changed: dict[int, ReactiveOwner] = field(default_factory=dict)
    changed_names: dict[int, set[str]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    hooks: list[tuple[object | None, Callable[[StateDelta], None]]] = field(default_factory=list)
    participants: dict[object, ActionParticipant] = field(default_factory=dict)
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

    def note_born(self, owner: object) -> None:
        """Record an object created after this transaction began."""
        self.born[id(owner)] = owner

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
            self.observed[cell] = cell.value

    def check_preconditions(self) -> None:
        """Reject the action if a cell it both read and wrote no longer holds what it read.

        Compare-and-set, derived from what the handler did rather than declared beside it.
        A cell that was only written carries no precondition, and last commit wins; a cell
        that was only read carries none either, because nothing was lost by looking.
        """
        for cell, seen in self.observed.items():
            if cell not in self.writes or _equal(cell.value, seen):
                continue
            address = cell.address
            message = (
                f"{address.owner!r}.{address.name} changed while this action was running: it "
                f"was read and then written, and the value it read is no longer there. Nothing "
                f"was published."
            )
            raise SharedStateConflictError(message)

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

    def enlist[ParticipantT: ActionParticipant](self, key: object, factory: Callable[[], ParticipantT]) -> ParticipantT:
        """Return this action's participant for `key`, creating it on first use.

        The guards run on every call, not just the first: a participant enlisted before a
        `block_writes` region may not keep staging writes inside one.
        """
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

    def delta(self) -> StateDelta:
        """What this action changed, both directions, from the overlay it already holds."""
        return StateDelta(
            tuple(
                StateChange(
                    entry.owner,
                    entry.name,
                    entry.before is not _MISSING,
                    None if entry.before is _MISSING else entry.before,
                    entry.value is not _MISSING,
                    None if entry.value is _MISSING else entry.value,
                )
                for entry in self.writes.values()
            )
        )

    def commit(self) -> None:
        """Publish the action, fallible half first.

        Everything that can fail runs while nothing is visible yet, so a caller that
        catches this can still roll the whole action back. `published` marks the crossing:
        past it the action has happened, and a later error is a failure to *report* it, not
        a reason to undo it.

        Commit hooks run last for that reason. A recorder's effect reaches outside the
        transaction -- `sl.history` pushes an entry -- and an entry describing an action
        that a later failure rolled back would be worse than a missing one.
        """
        # Before publication, because the guard compares against the value another action
        # left in the cell, and publishing would overwrite it with this action's own.
        self.check_preconditions()
        # Published before prepare, so a participant validates against the state the action
        # actually left. Nothing awaits between here and `published`, so no other task can
        # observe the window, and a failed prepare still rolls the whole action back.
        self.publish()
        for participant in self.participants.values():
            participant.prepare()
        delta = self.delta() if self.hooks else None
        self.published = True
        for participant in self.participants.values():
            participant.apply()
        for owner in self.changed.values():
            owner._state_changed(frozenset(self.changed_names[id(owner)]))
        for participant in self.participants.values():
            participant.finalize()
        if delta is not None:
            for _, callback in self.hooks:
                callback(delta)

    def rollback(self) -> None:
        for participant in self.participants.values():
            try:
                participant.abort()
            except Exception:
                # Whatever failed the action is the error worth propagating; a cleanup
                # failure raised over it would hide why the action failed at all.
                _log.exception("a transaction participant failed to abort")
        self.restore()


def report_undeclared_write(owner: object, name: str) -> None:
    """Reject a transaction-time write to an attribute that is not declared state.

    Raised before the write lands, so the transaction rolls back whole. The alternative --
    letting it land and logging that it is uncovered -- produced exactly the corruption it
    described: the attribute stays written while everything around it is restored.

    A component built during the action is exempt; assigning to something the action created
    is construction, not mutation.
    """
    current = _CURRENT.get()
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
        f"Declare it with sl.state()."
    )
    raise UndeclaredStateError(message)


@contextmanager
def transaction() -> Iterator[None]:
    """Coalesce state writes and roll all of them back when the block raises."""
    if _CURRENT.get() is not None:
        yield
        return
    current = _Transaction()
    token = _CURRENT.set(current)
    try:
        yield
    except BaseException:
        _CURRENT.reset(token)
        current.rollback()
        raise
    # Reset before committing, not after: the commit notifies owners, and a notification
    # that writes state must not be recorded by the transaction reporting on it.
    _CURRENT.reset(token)
    try:
        current.commit()
    except BaseException:
        # A commit that failed before publication is an action that did not happen.
        if not current.published:
            current.rollback()
        raise


@contextmanager
def batch() -> Iterator[None]:
    """Coalesce related state writes into one invalidation per component."""
    with transaction():
        yield


@contextmanager
def readonly_transaction() -> Iterator[None]:
    """Roll back and reject any state mutation within the block."""
    if _CURRENT.get() is not None:
        message = "a read-only transaction cannot nest inside a writable transaction"
        raise RuntimeError(message)
    current = _Transaction(readonly=True)
    token = _CURRENT.set(current)
    try:
        yield
    except BaseException:
        _CURRENT.reset(token)
        current.rollback()
        raise
    # Reset before committing, not after: the commit notifies owners, and a notification
    # that writes state must not be recorded by the transaction reporting on it.
    _CURRENT.reset(token)
    try:
        current.commit()
    except BaseException:
        # A commit that failed before publication is an action that did not happen.
        if not current.published:
            current.rollback()
        raise


def on_action_commit(callback: Callable[[StateDelta], None], *, key: object | None = None) -> None:
    """Hand `callback` the action's whole state delta if this transaction commits.

    The capture an undo entry needs is the one the transaction already took, completed with
    the post-action values at commit. A rolled-back action never reaches here, so a recorder
    needs no cleanup path. `key` identifies the recorder, and one recorder may register only
    once per action -- two hooks from the same owner would describe the same delta twice.
    """
    current = _CURRENT.get()
    if current is None:
        message = "commit hooks are only available inside an action's transaction"
        raise RuntimeError(message)
    if current.readonly:
        message = "a read-only action changed nothing, so it has nothing to record"
        raise ReactiveWriteError(message)
    if key is not None and has_action_hook(key):
        message = f"{key!r} already registered a commit hook for this action"
        raise RuntimeError(message)
    current.hooks.append((key, callback))


def join_action[ParticipantT: ActionParticipant](
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


def action_participant(key: object) -> ActionParticipant | None:
    """`key`'s participant in the action in flight, without enlisting one.

    The read half of :func:`join_action`, for a subsystem that has to answer "what did this
    action stage" on a path where staging itself would be wrong -- a read, or a render.
    """
    current = _CURRENT.get()
    return None if current is None else current.participants.get(key)


def has_action_hook(key: object) -> bool:
    """Whether `key` already registered a commit hook for the action in flight."""
    current = _CURRENT.get()
    return current is not None and any(registered is key for registered, _ in current.hooks)


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
                    f"so a write here tears: use sl.state(factory=...) for a lazy-initialized "
                    f"default, sl.computed for a value derived from state, sl.resource or "
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
    call. ``sl.watch()`` exists only for a named topic, which has no value to read.
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


def addresses(read: Callable[[], object]) -> tuple[Any, ...]:
    """The bus addresses of the shared cells `read` touches, for following them by hand.

    ``sl.addresses(lambda: preferences.theme)`` names a cell the way the rest of the package
    does -- by reading it -- so the thunk is ordinary typed code with no class name repeated
    and no string to drift. Reading a computed yields the shared cells behind it. Raises if
    the thunk reaches no shared cell at all, so a typo cannot quietly follow nothing.
    """
    with observe_render() as observation:
        read()
    found = observation.addresses()
    if not found:
        message = (
            "addresses() read no shared state: the callable must read at least one sl.state() "
            "off a namespace, directly or through a computed. Component state has no address."
        )
        raise ValueError(message)
    return found

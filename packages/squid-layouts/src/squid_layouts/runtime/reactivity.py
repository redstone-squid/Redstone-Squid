"""Transactional reactive state for component trees.

A state field stores an immutable value in a :class:`_Cell`, next to the version that dates
it. Writing replaces the value; nothing is mutated in place. That is what makes a snapshot a
reference rather than a deep copy, and rolling an action back putting the old reference back.
"""

import logging
from collections.abc import Callable, Iterator, Mapping
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
    """An undeclared attribute was written inside a transaction under strict mode."""


class MutableStateError(TypeError):
    """A state field was assigned a value that cannot be treated as a snapshot."""


def _reject(label: str, value: Any, error: TypeError) -> None:
    message = (
        f"{label} was assigned {type(value).__name__}, which is mutable. State is replaced, not "
        f"mutated: use a tuple, a frozenset, or a frozen dataclass. A collaborator this component "
        f"holds but never mutates is declared sl.state(..., opaque=True). ({error})"
    )
    raise MutableStateError(message) from error


def _check_value(label: str, value: Any) -> None:
    """Reject a state value that is not deeply immutable.

    Hashability is the test. It is not a perfect oracle -- a plain mutable object hashes by
    identity -- but it is *deep*, which is the property that matters: ``(1, [2])`` and a frozen
    dataclass with a ``list`` field both fail, and those are the cases that actually bite.
    """
    try:
        hash(value)
    except TypeError as error:
        _reject(label, value, error)


def _equal(left: Any, right: Any) -> bool:
    """Whether a write leaves the field where it already was, conservatively."""
    if left is right:
        return True
    try:
        return bool(left == right)
    except Exception:
        return False


def _frozen(value: Any) -> Any:
    """Return a restored snapshot value in the immutable shape it was exported from.

    JSON has one sequence type, so an exported tuple comes back as a list. Nothing else needs
    a case: a value still unhashable after this could not have been state to begin with, and
    the assignment says so.
    """
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


class _Cell:
    """One state field's storage: an immutable value, and the version that dates it."""

    __slots__ = ("value", "version")

    def __init__(self, value: Any = _MISSING, version: int = 0) -> None:
        self.value = value
        self.version = version


@dataclass(slots=True)
class _Snapshot:
    owner: ReactiveOwner
    name: str
    cell: _Cell
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
            _before(change.owner, change.name)
            _write_cell(change.owner, change.name, value)
            _after(change.owner, change.name)


def _write_cell(owner: ReactiveOwner, name: str, value: Any) -> None:
    """Install a value as a fresh write: the version moves forward, never back."""
    cell = owner.__dict__.get(name)
    if cell is None:
        owner.__dict__[name] = _Cell(value)
        return
    cell.value = value
    cell.version += 1


@dataclass(slots=True)
class _Transaction:
    readonly: bool = False
    snapshots: dict[tuple[int, str], _Snapshot] = field(default_factory=dict)
    changed: dict[int, ReactiveOwner] = field(default_factory=dict)
    changed_names: dict[int, set[str]] = field(default_factory=dict)
    # Held by strong reference, so an id cannot be recycled while this transaction runs.
    born: dict[int, object] = field(default_factory=dict)
    hooks: list[tuple[object | None, Callable[[StateDelta], None]]] = field(default_factory=list)
    participants: dict[object, ActionParticipant] = field(default_factory=dict)
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

    def record(self, owner: ReactiveOwner, name: str) -> None:
        if not self.protects(owner):
            return
        key = (id(owner), name)
        if key in self.snapshots:
            return
        cell = owner.__dict__.get(name)
        if cell is None:
            # The cell outlives every value it holds, so a reader that recorded it keeps
            # seeing this field even across a restore that puts the field back to unassigned.
            cell = owner.__dict__[name] = _Cell()
        self.snapshots[key] = _Snapshot(owner, name, cell, cell.value, cell.version)

    def mark_changed(self, owner: ReactiveOwner, name: str) -> None:
        if not self.protects(owner):
            return
        if self.write_block is not None:
            raise ReactiveWriteError(self.write_block)
        if self.readonly:
            message = "parallel-read actions cannot mutate component state"
            raise ReactiveWriteError(message)
        self.changed[id(owner)] = owner
        self.changed_names.setdefault(id(owner), set()).add(name)

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
        """What this action changed, both directions, from the snapshots it already took."""
        changes: list[StateChange] = []
        for snapshot in self.snapshots.values():
            after = snapshot.cell.value
            changes.append(
                StateChange(
                    snapshot.owner,
                    snapshot.name,
                    snapshot.value is not _MISSING,
                    None if snapshot.value is _MISSING else snapshot.value,
                    after is not _MISSING,
                    None if after is _MISSING else after,
                )
            )
        return StateDelta(tuple(changes))

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
        for snapshot in reversed(tuple(self.snapshots.values())):
            # The version goes back too, not forward: a reader that sampled this cell before
            # the action is still valid, and bumping would make it recompute for nothing.
            snapshot.cell.value = snapshot.value
            snapshot.cell.version = snapshot.version
            snapshot.owner._state_rolled_back()


_CURRENT: ContextVar[_Transaction | None] = ContextVar("squid_layouts_transaction", default=None)
_STRICT: ContextVar[bool] = ContextVar("squid_layouts_strict_state", default=False)


@contextmanager
def strict_state(*, enabled: bool = True) -> Iterator[None]:
    """Turn transaction-time writes to undeclared attributes into errors."""
    token = _STRICT.set(enabled)
    try:
        yield
    finally:
        _STRICT.reset(token)


def report_undeclared_write(owner: object, name: str) -> None:
    """Report a transaction-time write to an attribute that is not declared state.

    Read-only actions reject it. Writable ones let the write land but say it is uncovered,
    rather than pretending a half-guarantee reaches it.
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
        f"{label} was assigned inside a transaction but is not declared state: it will not be "
        f"rolled back if the action fails, and it will not trigger a re-render. "
        f"Declare it with sl.state()."
    )
    if _STRICT.get():
        raise UndeclaredStateError(message)
    _log.warning(message)


def _before(owner: ReactiveOwner, name: str) -> None:
    if current := _CURRENT.get():
        current.record(owner, name)


def _after(owner: ReactiveOwner, name: str) -> None:
    if current := _CURRENT.get():
        current.mark_changed(owner, name)
    else:
        owner._state_changed(frozenset((name,)))


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

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = f"__state_{name}"
        self.public_name = name
        if not self.opaque and self._default is not _MISSING:
            _check_value(f"{owner.__name__}.{name}", self._default)

    @property
    def has_initial(self) -> bool:
        return self._default is not _MISSING or self._factory is not None

    def _initial(self, instance: ReactiveOwner) -> Any:
        if self._factory is None:
            # No copy: the default is immutable, so every instance may share the one object.
            return self._default
        value = self._factory()
        if not self.opaque:
            _check_value(f"{type(instance).__name__}.{self.public_name}", value)
        return value

    def cell(self, instance: ReactiveOwner) -> _Cell:
        """Return this field's storage on `instance`, empty until something assigns it."""
        cell = instance.__dict__.get(self._name)
        if cell is None:
            cell = instance.__dict__[self._name] = _Cell()
        return cell

    def is_set(self, instance: ReactiveOwner) -> bool:
        """Whether this field currently holds a value of its own rather than its default."""
        cell = instance.__dict__.get(self._name)
        return cell is not None and cell.value is not _MISSING

    def mutated(self, instance: ReactiveOwner) -> None:
        """Note that the held value changed in place: the version moves, the value does not."""
        self.cell(instance).version += 1

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cell = instance.__dict__.get(self._name)
        if cell is None or cell.value is _MISSING:
            if not self.has_initial:
                message = f"{type(instance).__name__}.{self.public_name} was never assigned"
                raise AttributeError(message)
            # Materializing a default is not a write: no version bump, no invalidation.
            if cell is None:
                cell = instance.__dict__[self._name] = _Cell(self._initial(instance))
            else:
                cell.value = self._initial(instance)
        return cell.value

    def __set__(self, instance: ReactiveOwner, value: Any) -> None:
        if not self.opaque:
            try:
                hash(value)
            except TypeError as error:
                _reject(f"{type(instance).__name__}.{self.public_name}", value, error)
        cell = instance.__dict__.get(self._name)
        if cell is not None and (cell.value is value if self.opaque else _equal(cell.value, value)):
            # Opaque fields compare by identity: their values are collaborators, and `==` on
            # one is the author's code, not a cheap settled-value check.
            return
        _before(instance, self._name)
        cell = self.cell(instance)
        cell.value = value
        cell.version += 1
        _after(instance, self._name)


@overload
def state[ValueT](
    default: ValueT,
    *,
    persist: bool | None = None,
    opaque: bool = False,
) -> ValueT: ...


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
    assigns. Values are immutable and replaced rather than mutated, which every assignment
    checks. ``opaque=True`` declares a collaborator the component holds and never mutates --
    a service, a guild, a session -- and skips that check for it.
    """
    return _State(default, factory=factory, persist=persist, opaque=opaque)


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


class _Computed:
    def __init__(self, function: Callable[[Any], Any], *, depends: tuple[object, ...]) -> None:
        self._function = function
        self.depends = depends
        self.public_name = function.__name__
        self._name = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__computed_{name}"

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        if self._name not in instance.__dict__:
            instance.__dict__[self._name] = self._function(instance)
        return instance.__dict__[self._name]

    def invalidate_for(self, instance: ReactiveOwner) -> None:
        """Discard an already-materialized value after a dependency commit."""
        instance.__dict__.pop(self._name, None)

    def refresh_for(self, instance: ReactiveOwner) -> bool:
        """Refresh a materialized value and report whether downstream inputs changed."""
        if self._name not in instance.__dict__:
            return True
        previous = instance.__dict__.pop(self._name)
        try:
            current = self._function(instance)
        except Exception:
            return True
        instance.__dict__[self._name] = current
        return not _equal(current, previous)


def computed(*, depends: tuple[object, ...]) -> Callable[[Callable[[Any], Any]], _Computed]:
    """Cache a derived value until one of its declared state dependencies changes."""

    def decorate(function: Callable[[Any], Any]) -> _Computed:
        return _Computed(function, depends=depends)

    return decorate

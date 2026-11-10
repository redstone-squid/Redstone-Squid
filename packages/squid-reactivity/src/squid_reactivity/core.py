"""Declare reactive models and cache their derived values.

State descriptors delegate writes to transactions and reads to tracked cells.
Computed descriptors retain their dependencies and validate them on demand.
Models also expose inspection and state export/restore for host integrations.
"""

import functools
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, ClassVar, Self, overload, override

import squid_reactivity.tracking as tracking
from squid_reactivity.addresses import CellAddress, publish
from squid_reactivity.tracking import (
    _CONSUMER,
    _CURRENT,
    _MISSING,
    _READ_ONLY,
    _RELAXED_READS,
    _STRONG_READS,
    ReactiveOwner,
    _active,
    _Cell,
    _check_write,
    _equal,
    _ReactiveSource,
    _staged_value,
    read_only,
    settling,
    untracked,
)
from squid_reactivity.transactions import (
    _write,
    report_undeclared_write,
    transaction,
)

_log = logging.getLogger(__name__)


def _frozen(value: Any) -> Any:
    """Return a restored snapshot value in the shape it was exported from.

    JSON has one sequence type, so an exported tuple comes back as a list. A field declared
    `Sequence` reads either; one declared `tuple[...]` needs the tuple back.
    """
    if isinstance(value, list | tuple):
        return tuple(_frozen(item) for item in value)
    return value


class _State:
    """Descriptor backing a declared reactive state field."""

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

    def cell(self, instance: ReactiveOwner) -> _Cell:
        """Return this field's storage on `instance`, empty until something assigns it."""
        cell = instance.__dict__.get(self._name)
        if cell is None:
            cell = instance.__dict__[self._name] = _Cell(address=CellAddress(instance, self.public_name))
        return cell

    def is_set(self, instance: ReactiveOwner) -> bool:
        """Whether this field currently holds a value of its own rather than its default."""
        cell = instance.__dict__.get(self._name)
        return cell is not None and (cell.value is not _MISSING or _staged_value(cell) is not _MISSING)

    def mutated(self, instance: ReactiveOwner) -> None:
        """Stage a version change for an opaque value that was mutated in place.

        The mutation itself cannot be undone. Staging delays its notification until
        commit, so a failed action does not publish a change notification. Bypass
        __set__ because its identity comparison would discard this same-object write.
        """
        cell = self.cell(instance)
        held = _staged_value(cell)
        if held is _MISSING:
            held = cell.value
        _write(instance, self._name, cell, held)

    def __get__(self, instance: ReactiveOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        cell = instance.__dict__.get(self._name)
        # `read()` answers with this action's staged value wherever it has one, so a slot the
        # action emptied reads as empty rather than handing back the sentinel that spells it.
        # Asking the cell first is what keeps that sentinel private: deciding on `cell.value`
        # and only then reading meant a staged absence was returned to the caller verbatim.
        held = _MISSING if cell is None else cell.read()
        if held is not _MISSING:
            return held
        if not self.has_initial:
            message = f"{type(instance).__name__}.{self.public_name} was never assigned"
            raise AttributeError(message)
        cell = self.cell(instance)
        if cell.value is _MISSING:
            # Materializing a default is not a write: no version bump, no invalidation.
            cell.value = self._initial(instance)
            return cell.read()
        # The action in flight emptied this slot. Empty reads as the declared default, and
        # that default is not installed over the committed value: outside the action the
        # committed value is still what everyone sees, and the commit is about to remove it
        # rather than replace it with this.
        return self._initial(instance)

    def __set__(self, instance: ReactiveOwner, value: Any) -> None:
        _check_write(instance, self.public_name)
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

    def __delete__(self, instance: ReactiveOwner) -> None:
        """Reject deletion and direct callers to reset the field by assignment.

        History can still restore an unassigned slot through the internal write
        path, preserving its identity and version.
        """
        message = (
            f"{type(instance).__name__}.{self.public_name} cannot be deleted; a state field is "
            f"reset, not removed. Assign the value it should hold instead."
        )
        raise AttributeError(message)


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
    """Declare a reactive field whose assignments participate in transactions.

    Supply either a default or a factory, or leave both out and assign the field
    in __init__. The factory runs when an unassigned field is first read.

    Replace ordinary values rather than mutate them in place. For example, use
    ``self.items = (*self.items, item)`` to notify readers of an added item.
    Collection overloads expose read-only interfaces to type checkers, but the
    actual stored objects are not wrapped or copied.

    Args:
        default: Initial value, shared by reference across instances.
        factory: Build an initial value for each instance on demand.
        persist: Include the field in exported state. Defaults to True unless opaque.
        opaque: Hold a collaborator such as a service or session. Compare changes
            by identity and exclude the field from persistence.
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


def _descriptors[DescriptorT](cls: type, kind: type[DescriptorT]) -> Iterator[tuple[str, DescriptorT]]:
    """Visit matching declarations base-first, retaining overridden descriptors.

    Callers decide which declarations to keep after applying their own filters.
    Visiting every match also preserves lazy cell/node creation during inspection.
    """
    for base in reversed(cls.__mro__):
        for name, descriptor in vars(base).items():
            if isinstance(descriptor, kind):
                yield name, descriptor


def inspect_cells(owner: ReactiveOwner) -> dict[str, CellReport]:
    """Report every declared state field on `owner`, by public name."""
    reports: dict[str, CellReport] = {}
    for name, descriptor in _descriptors(type(owner), _State):
        cell = descriptor.cell(owner)
        reports[name] = CellReport(id(cell), cell.settle(), descriptor.is_set(owner), descriptor.opaque)
    return reports


def inspect_computed(owner: ReactiveOwner) -> dict[str, ComputedReport]:
    """Report every computed on `owner`, without evaluating one that has never run."""
    reports: dict[str, ComputedReport] = {}
    for name, descriptor in _descriptors(type(owner), _Computed):
        node = descriptor.node(owner)
        reports[name] = ComputedReport(id(node), node.evaluated, node.version, tuple(map(id, node.sources)))
    return reports


def declared_cells(owner: ReactiveOwner) -> dict[_Cell, int]:
    """Return all declared cells and their current versions.

    Resources use these as initial dependencies when replace supplies a value
    before the loader has run. The first load records the actual dependencies.
    """
    presumed: dict[_Cell, int] = {}
    for klass in type(owner).__mro__:
        for descriptor in vars(klass).values():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                presumed[cell] = cell.settle()
    return presumed


def _state_fields(owner: ReactiveOwner) -> dict[str, _State]:
    return {name: descriptor for name, descriptor in _descriptors(type(owner), _State) if descriptor.persist}


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

    __slots__ = ("_epoch", "_function", "_label", "_settled", "_view", "owner", "sources", "value", "version")

    def __init__(self, function: Callable[[Any], Any], owner: ReactiveOwner, label: str) -> None:
        self._function = function
        self._label = label
        self.owner = owner
        self.sources: dict[_ReactiveSource, int] = {}
        self.value: Any = None
        self.version = 0
        self._settled = False
        self._epoch = -1
        self._view: object = _MISSING

    @property
    def address(self) -> None:
        return None

    def dependencies(self) -> Iterable[_ReactiveSource]:
        return self.sources

    def settle(self) -> int:
        """Return the version after validating this read view and its dependencies."""
        current = _active()
        view = None if current is None else (current.read_view, _RELAXED_READS.get() > 0, _STRONG_READS.get() > 0)
        # A cache belongs to one read view. Re-evaluate on entry to an action or
        # a different read policy so cache hits cannot bypass conflict observations.
        if self._view == view:
            if self._epoch == tracking._EPOCH:
                return self.version
            if self._settled and all(source.settle() == seen for source, seen in self.sources.items()):
                self._epoch = tracking._EPOCH
                return self.version
        # Cleared before the run, not after: a body that raises must leave the node asking to
        # be recomputed rather than holding a value nothing verified.
        self._settled = False
        self._epoch = -1
        self.sources = {}
        with _CONSUMER.set(self), settling(self), read_only():
            value = self._function(self.owner)
        self._settled = True
        self._view = view
        # Recomputing is not a write, so the epoch stays put: nothing downstream can be stale
        # from it, and bumping would make every settled node in the process walk its sources.
        self._epoch = tracking._EPOCH
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
    """Cache a derived value and track the reactive fields its function reads.

    The function runs on first access, when dependencies change, and when the
    transaction or its read consistency policy changes. One cache serves the
    current read view; switching views recomputes so staged values stay isolated
    and action reads are recorded even if another view already evaluated it.

    Each run replaces the dependencies, including conditional branches. Equal
    results preserve the computed version. The function runs read-only.
    """
    # The declared return type describes what reading the attribute yields, not what the
    # decorator hands back: the descriptor below is what actually gets bound to the class.
    return _Computed(function)  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-return]


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
                vars(self).pop("_reactive_initialization", None)

    return __init__


class Reactive:
    """Reusable reactive state and behavior, independent of rendering.

    Declare fields with `state()` and derived properties with `computed`. Consumers
    automatically follow fields they read through a subscription reconciler. Fields without
    a default must be assigned before initialization finishes.

    Construction may assign ordinary attributes. Once the owner exists, writes
    inside a transaction must target declared state so failure can discard them.
    Opaque collaborator changes are signaled through `mutated`; that delays their
    notifications but cannot undo mutations already made to the collaborator.
    """

    _reactive_commit_listeners: set[Callable[[], None]]

    _state_names: ClassVar[frozenset[str]] = frozenset()
    _state_descriptors: ClassVar[dict[str, _State]] = {}
    _opaque_state: ClassVar[tuple[tuple[str, _State], ...]] = ()
    _computed_descriptors: ClassVar[dict[str, _Computed]] = {}
    _reactive_internal_attributes: ClassVar[frozenset[str]] = frozenset()
    _reactive_require_state: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared = dict(_descriptors(cls, _State))
        cls._state_names = frozenset(declared)
        cls._state_descriptors = declared
        cls._opaque_state = tuple((name, descriptor) for name, descriptor in declared.items() if descriptor.opaque)
        cls._computed_descriptors = dict(_descriptors(cls, _Computed))
        required = tuple((name, descriptor) for name, descriptor in declared.items() if not descriptor.has_initial)
        cls.__init__ = _checked_init(cls.__init__, required if cls._reactive_require_state else ())

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        instance = super().__new__(cls)
        # Subclasses can initialize their own inputs without calling a base initializer.
        object.__setattr__(instance, "_reactive_commit_listeners", set())
        object.__setattr__(instance, "_reactive_initialization", _READ_ONLY.get())
        if current := _CURRENT.get():
            current.note_born(instance)
        return instance

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if (
            _active() is not None
            and name not in type(self)._reactive_internal_attributes
            and name not in type(self)._state_names
        ):
            report_undeclared_write(self, name)
        object.__setattr__(self, name, value)

    def _state_rolled_back(self) -> None:
        self.on_state_rollback()

    def on_state_commit(self, names: frozenset[str]) -> None:
        """React after the named state fields publish together."""

    def on_state_rollback(self) -> None:
        """React after an attempted transaction restores this owner."""

    def mutated(self, collaborator: object) -> None:
        """Signal an in-place change to the object held by one opaque state field.

        Inside an action the signal stages with it, so a handler that fails publishes no
        notification and a namespace does not tell other mounts to re-read halfway through
        a transaction that is about to roll back.
        """
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
        # No notification of its own: the staged write carries one, delivered at the commit
        # outside an action and at `finalize_commit` inside one.
        descriptor.mutated(self)

    def _state_changed(self, names: frozenset[str]) -> None:
        for descriptor in type(self)._state_descriptors.values():
            if descriptor._name in names:
                publish(CellAddress(self, descriptor.public_name))
        for listener in tuple(self._reactive_commit_listeners):
            try:
                listener()
            except Exception:
                _log.exception("reactive commit listener failed")
        self.on_state_commit(names)

    def _add_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.add(listener)

    def _remove_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.discard(listener)

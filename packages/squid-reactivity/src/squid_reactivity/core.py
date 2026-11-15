"""Declare reactive models, their state fields, and cached computations.

A state descriptor stores each instance's value in a cell. A computed descriptor
records the inputs read by its function and checks their versions before reusing
a result. Reactive supplies transaction-aware assignment and change notifications.
Inspection and export helpers expose model state without requiring a UI framework.
"""

import functools
import logging
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from typing import Any, ClassVar, Self, cast, overload, override

from squid_reactivity._runtime import _runtime
from squid_reactivity.addresses import CellAddress, SubscriberRegistry, publish
from squid_reactivity.cells import _MISSING, _Cell, _Missing
from squid_reactivity.evaluation import _SettlingNode, settling
from squid_reactivity.tracking import _DEPENDENCY_COLLECTOR, _DependencyCollector, _ReactiveSource, untracked
from squid_reactivity.transactions import transaction
from squid_reactivity.transactions.engine import _write, report_undeclared_write
from squid_reactivity.transactions.state import (
    _READ_CONSISTENCY,
    _READ_ONLY,
    ReactiveStateOwner,
    _active,
    _check_write,
    _current_transaction,
    read_only,
)

_log = logging.getLogger(__name__)


def _equal(left: object, right: object) -> bool:
    """Compare values, treating an equality-check failure as a difference."""
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


class _State[ValueT]:
    """Define one field and manage its per-instance cell, defaults, and assignments."""

    def __init__(
        self,
        default: ValueT | _Missing = _MISSING,
        *,
        factory: Callable[[], ValueT] | None = None,
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

    def _initial(self, instance: ReactiveStateOwner) -> ValueT:
        if self._factory is None:
            # No copy: a state value is replaced, never mutated, so instances may share it.
            assert self._default is not _MISSING
            return self._default
        return self._factory()

    def cell(self, instance: ReactiveStateOwner) -> _Cell[ValueT]:
        """Return this field's storage on `instance`, empty until something assigns it."""
        cell = cast(_Cell[ValueT] | None, instance.__dict__.get(self._name))
        if cell is None:
            cell = instance.__dict__[self._name] = _Cell[ValueT](address=CellAddress(instance, self.public_name))
        return cell

    def is_set(self, instance: ReactiveStateOwner) -> bool:
        """Whether this field currently holds a value of its own rather than its default."""
        cell = cast(_Cell[ValueT] | None, instance.__dict__.get(self._name))
        return cell is not None and (cell.value is not _MISSING or cell.staged_value() is not _MISSING)

    def mutated(self, instance: ReactiveStateOwner) -> None:
        """Stage a version change for an opaque value that was mutated in place.

        The mutation itself cannot be undone. Staging delays its notification until
        commit, so a failed transaction does not publish a change notification. Bypass
        __set__ because its identity comparison would discard this same-object write.
        """
        cell = self.cell(instance)
        held = cell.staged_value()
        if held is _MISSING:
            held = cell.value
        _write(instance, self._name, cell, held)

    @overload
    def __get__(self, instance: None, owner: type | None = None) -> _State[ValueT]: ...

    @overload
    def __get__(self, instance: ReactiveStateOwner, owner: type | None = None) -> ValueT: ...

    def __get__(self, instance: ReactiveStateOwner | None, owner: type | None = None) -> ValueT | _State[ValueT]:
        if instance is None:
            return self
        cell = cast(_Cell[ValueT] | None, instance.__dict__.get(self._name))
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
            value = cell.read()
            assert value is not _MISSING
            return value
        # The action in flight emptied this slot. Empty reads as the declared default, and
        # that default is not installed over the committed value: outside the action the
        # committed value is still what everyone sees, and the commit is about to remove it
        # rather than replace it with this.
        return self._initial(instance)

    def __set__(self, instance: ReactiveStateOwner, value: ValueT) -> None:
        _check_write(instance, self.public_name)
        cell = cast(_Cell[ValueT] | None, instance.__dict__.get(self._name))
        if cell is not None:
            held = cell.staged_value()
            if held is _MISSING:
                held = cell.value
            # Opaque fields compare by identity: their values are collaborators, and `==` on
            # one is the author's code, not a cheap settled-value check.
            if held is value if self.opaque else _equal(held, value):
                return
        _write(instance, self._name, self.cell(instance), value)

    def __delete__(self, instance: ReactiveStateOwner) -> None:
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
    """Describe the storage and assignment status of one declared field."""

    identity: int
    """`id()` of the cell, so a computed's sources can be resolved back to a name."""
    version: int
    assigned: bool
    """Whether the field holds a value of its own rather than its declared default."""
    opaque: bool


@dataclass(frozen=True, slots=True)
class ComputedReport:
    """Describe a cached computation and the identities of its direct dependencies."""

    identity: int
    evaluated: bool
    """False until something reads it. An unread computed value is never evaluated."""
    version: int
    sources: tuple[int, ...]
    """Identities of the cells and computeds its last run read, in read order."""


def _descriptors[DescriptorT](cls: type, kind: type[DescriptorT]) -> Iterator[tuple[str, DescriptorT]]:
    """Visit declarations base-first, including overrides from derived classes.

    Callers that build dictionaries by name keep the most derived declaration.
    """
    for base in reversed(cls.__mro__):
        for name, descriptor in vars(base).items():
            if isinstance(descriptor, kind):
                yield name, descriptor


def inspect_cells(owner: ReactiveStateOwner) -> dict[str, CellReport]:
    """Report every declared state field on `owner`, by public name."""
    reports: dict[str, CellReport] = {}
    for name, descriptor in _descriptors(type(owner), _State):
        cell = descriptor.cell(owner)
        reports[name] = CellReport(id(cell), cell.settle(), descriptor.is_set(owner), descriptor.opaque)
    return reports


def inspect_computed(owner: ReactiveStateOwner) -> dict[str, ComputedReport]:
    """Report every computed on `owner`, without evaluating one that has never run."""
    reports: dict[str, ComputedReport] = {}
    for name, descriptor in _descriptors(type(owner), _Computed):
        node = descriptor.node(owner)
        reports[name] = ComputedReport(id(node), node.evaluated, node.version, tuple(map(id, node.sources)))
    return reports


def declared_cells(owner: ReactiveStateOwner) -> dict[_Cell[Any], int]:
    """Return all declared cells and their current versions.

    Resources use these as initial dependencies when replace supplies a value
    before the loader has run. The first load records the actual dependencies.
    """
    presumed: dict[_Cell[Any], int] = {}
    for klass in type(owner).__mro__:
        for descriptor in vars(klass).values():
            if isinstance(descriptor, _State):
                cell = descriptor.cell(owner)
                presumed[cell] = cell.settle()
    return presumed


def _state_fields(owner: ReactiveStateOwner) -> dict[str, _State[Any]]:
    return {name: descriptor for name, descriptor in _descriptors(type(owner), _State) if descriptor.persist}


def export_state(owner: ReactiveStateOwner) -> dict[str, Any]:
    """Return the value of every persistent state descriptor that currently has one."""
    return {
        name: getattr(owner, name)
        for name, descriptor in _state_fields(owner).items()
        if descriptor.has_initial or descriptor.is_set(owner)
    }


def restore_state(owner: ReactiveStateOwner, values: Mapping[str, Any]) -> None:
    """Restore declared persistent state, rejecting stale or misspelled field names."""
    fields = _state_fields(owner)
    unknown = set(values) - set(fields)
    if unknown:
        message = f"snapshot has unknown state fields: {', '.join(sorted(unknown))}"
        raise ValueError(message)
    with transaction():
        for name, value in values.items():
            setattr(owner, name, _frozen(value))


class _Derived(_ReactiveSource, _DependencyCollector, _SettlingNode):
    """Cache one model's computed value and the versions of its dependencies.

    The node retains its input sources, but sources do not retain the node. Checking
    versions on demand lets unused computations be collected without unsubscribe work.
    """

    __slots__ = ("_epoch", "_function", "_label", "_settled", "_view", "owner", "sources", "value", "version")

    def __init__(self, function: Callable[[Any], Any], owner: ReactiveStateOwner, label: str) -> None:
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
    @override
    def address(self) -> None:
        return None

    @override
    def dependencies(self) -> Iterable[_ReactiveSource]:
        return self.sources

    @override
    def settle(self) -> int:
        """Return the version after validating this read view and its dependencies."""
        current = _active()
        view = None if current is None else (current.read_view, _READ_CONSISTENCY.get())
        # A cache belongs to one read view. Re-evaluate on entry to an action or
        # a different read policy so cache hits cannot bypass conflict observations.
        if self._view == view:
            if self._epoch == _runtime.epoch:
                return self.version
            if self._settled and all(source.settle() == seen for source, seen in self.sources.items()):
                self._epoch = _runtime.epoch
                return self.version
        # Cleared before the run, not after: a body that raises must leave the node asking to
        # be recomputed rather than holding a value nothing verified.
        self._settled = False
        self._epoch = -1
        self.sources = {}
        with _DEPENDENCY_COLLECTOR.set(self), settling(self), read_only():
            value = self._function(self.owner)
        self._settled = True
        self._view = view
        # Recomputing is not a write, so the epoch stays put: nothing downstream can be stale
        # from it, and bumping would make every settled node on the thread walk its sources.
        self._epoch = _runtime.epoch
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
        consumer = _DEPENDENCY_COLLECTOR.get()
        if consumer is not None:
            consumer.sources[self] = self.version
        return self.value


class _Computed:
    """Bind a computed function to a separate cache on each model instance."""

    def __init__(self, function: Callable[[Any], Any]) -> None:
        self._function = function
        self.public_name = function.__name__
        self._name = f"__computed_{function.__name__}"
        self._label = function.__name__

    def __set_name__(self, owner: type, name: str) -> None:
        self.public_name = name
        self._name = f"__computed_{name}"
        self._label = f"{owner.__name__}.{name}"

    def node(self, instance: ReactiveStateOwner) -> _Derived:
        """Return this computed's node on `instance`, unevaluated until something reads it."""
        node = instance.__dict__.get(self._name)
        if node is None:
            node = instance.__dict__[self._name] = _Derived(self._function, instance, self._label)
        return node

    def __get__(self, instance: ReactiveStateOwner | None, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return self.node(instance).read()


def computed[ValueT](function: Callable[[Any], ValueT]) -> ValueT:
    """Cache a derived value and track the reactive fields its function reads.

    The function runs on first access, when dependencies change, and when the
    transaction or its read consistency policy changes. One cache serves the
    current read view; switching views recomputes so staged values stay isolated
    and transaction reads are recorded even if another view already evaluated it.

    Each run replaces the dependencies, including conditional branches. Equal
    results preserve the computed version. The function runs read-only.
    """
    # The declared return type describes what reading the attribute yields, not what the
    # decorator hands back: the descriptor below is what actually gets bound to the class.
    return _Computed(function)  # pyright: ignore[reportReturnType]  # pyrefly: ignore[bad-return]


def _checked_init(
    original: Callable[..., None],
    required: tuple[tuple[str, _State[Any]], ...],
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


class Reactive(ReactiveStateOwner):
    """A model with tracked fields, cached computations, and transaction-aware writes.

    Declare fields with state() and derived properties with computed(). Consumers can
    capture reads and subscribe to the resulting addresses through Subscriptions.
    Fields without defaults must be assigned before initialization finishes.

    Construction may assign ordinary attributes. Later writes inside a transaction
    must target declared state so failure can discard them. Use mutated() to notify
    readers after changing an opaque collaborator in place; rollback can discard that
    notification, but cannot undo the collaborator's mutation.
    """

    _reactive_commit_listeners: set[Callable[[], None]]
    _reactive_subscribers: SubscriberRegistry

    _state_names: ClassVar[frozenset[str]] = frozenset()
    _state_descriptors: ClassVar[dict[str, _State[Any]]] = {}
    _opaque_state: ClassVar[tuple[tuple[str, _State[Any]], ...]] = ()
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
        object.__setattr__(instance, "_reactive_subscribers", {})
        object.__setattr__(instance, "_reactive_commit_listeners", set())
        object.__setattr__(instance, "_reactive_initialization", _READ_ONLY.get())
        if current := _current_transaction():
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

    @override
    def _state_rolled_back(self) -> None:
        self.on_state_rollback()

    def on_state_commit(self, names: frozenset[str]) -> None:
        """React after state fields publish together. `names` holds their declared field names."""

    def on_state_rollback(self) -> None:
        """React after this model's staged writes are discarded."""

    def mutated(self, collaborator: object) -> None:
        """Notify readers after changing an object held in an opaque state field.

        Inside a transaction, notification waits for commit and is discarded on rollback.
        The object's in-place mutation itself is not reversible.
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

    @override
    def _state_changed(self, names: frozenset[str]) -> None:
        # Transactions identify a write by its storage key (`__state_<field>`). Subscribers and
        # `on_state_commit` are addressed by the field name the author declared.
        changed = [
            descriptor.public_name for descriptor in type(self)._state_descriptors.values() if descriptor._name in names
        ]
        for name in changed:
            publish(CellAddress(self, name))
        for listener in tuple(self._reactive_commit_listeners):
            try:
                listener()
            except Exception:
                _log.exception("reactive commit listener failed")
        self.on_state_commit(frozenset(changed))

    def _add_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.add(listener)

    def _remove_commit_listener(self, listener: Callable[[], None]) -> None:
        self._reactive_commit_listeners.discard(listener)

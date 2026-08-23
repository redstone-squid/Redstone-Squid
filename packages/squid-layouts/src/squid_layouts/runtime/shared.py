"""View state that outlives one mount.

A shared namespace holds what the view owns. It is a class, declared the way a component
declares its own state, and two panels converge on one because something handed them the
same object -- there is no store, no registry and no keyed lookup. What a namespace adds
over an application service is that its writes join the action's transaction and its
changes reach the bus and :mod:`~squid_layouts.runtime.history` without the author writing
either half.

Anything the application would still want if no one were looking at it belongs to the
application's data layer, not here.
"""

from collections.abc import Callable
from typing import Any, ClassVar

from squid_layouts.runtime.reactivity import (
    _Computed,
    _State,
)
from squid_layouts.runtime.resources import _ResourceDescriptor
from squid_layouts.runtime.topics import Address, CellAddress, TopicBus

_RESERVED = frozenset({"bus", "scope"})
"""Attribute names a namespace owns, so a cell may not take one.

Everything beginning with an underscore is reserved too. The list is short on purpose: the
surface is a read, a write and `scope`, and every name past those is the author's.
"""

_NO_SCOPE: Any = None
"""The scope of a namespace with nothing to say about itself, i.e. ``Shared[None]``."""


def _check_name(cls: type, name: str) -> None:
    if name in _RESERVED or name.startswith("_"):
        message = f"{cls.__name__}.{name}: a namespace reserves {name!r} and every underscored name"
        raise TypeError(message)


class Shared[ScopeT = None]:
    """Base class for a namespace of view state that several mounts share.

    Subclass it, declare cells with :func:`cell`, and hand the instance to whoever should see
    the same values. The handle *is* the state, so it lives exactly as long as the object
    does: panels holding it means the state dies with the last panel, and a session holding
    it means the state survives every panel opening and closing. Which of those you want is
    a line of host code, not a setting.

    ``ScopeT`` is a label, and nothing is required of it -- not frozen, not hashable, not
    validated. Nothing keys on it, because nothing keys on anything; it exists so a conflict
    message, a history label and a devtools row can say which namespace they mean.

    Args:
        bus: The host's topic bus, which cell changes are published on. Required, because a
            namespace that silently stopped being reactive would be worse than one that
            cannot be built.
        scope: What this namespace is about, for diagnostics.
    """

    _cells: ClassVar[dict[str, _State]] = {}
    """Declared state by public name."""
    _slots: ClassVar[dict[str, _State]] = {}
    """The same fields by storage name, which is what a commit reports changed."""
    _resources: ClassVar[frozenset[str]] = frozenset()
    """Declared resources by public name. Addressed like cells, but loaded rather than written."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared: dict[str, _State] = {}
        loaded: set[str] = set()
        for klass in reversed(cls.__mro__):
            for name, descriptor in vars(klass).items():
                if isinstance(descriptor, _ResourceDescriptor):
                    _check_name(cls, name)
                    loaded.add(name)
                    continue
                if isinstance(descriptor, _Computed):
                    _check_name(cls, name)
                    continue
                if not isinstance(descriptor, _State):
                    continue
                _check_name(cls, name)
                if descriptor.persist_declared and descriptor.persist:
                    message = (
                        f"{cls.__name__}.{name}: a namespace is never persisted, so persist=True "
                        f"cannot be honoured. Its lifetime is whoever holds the handle."
                    )
                    raise TypeError(message)
                declared[name] = descriptor
        cls._cells = declared
        cls._slots = {descriptor._name: descriptor for descriptor in declared.values()}
        cls._resources = frozenset(loaded)

    def _state_binding(self, name: str) -> CellAddress:
        """Address a field declared here, so a write publishes instead of staying local.

        The one hook that makes `sl.state()` on a namespace mean something different from
        `sl.state()` on a component -- and the reason it no longer has to be spelled
        differently. A component has no such hook, so its state has no address.
        """
        return CellAddress(self, name)

    def _resource_binding(self, name: str) -> tuple[CellAddress, Callable[[Any], None]]:
        """Address a resource declared here, and hand it the bus to announce itself on.

        This is what makes a namespace resource *shared* rather than merely reachable from
        several places: a component's resource reloads and re-renders its one component,
        while this one reloads and publishes, so every mount that read it re-reads.
        """
        return CellAddress(self, name), self.bus.publish

    def __init__(self, bus: TopicBus, scope: ScopeT = _NO_SCOPE) -> None:
        self.bus = bus
        self.scope = scope
        # Eagerly, so every cell carries its address from birth. A cell outlives every value
        # it holds and is never replaced, so this is the only place one has to be made.
        for descriptor in type(self)._cells.values():
            descriptor.cell(self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in type(self)._cells and name not in _RESERVED and not name.startswith("_"):
            message = (
                f"{type(self).__name__}.{name} is not declared state. A namespace holds view state "
                f"declared with sl.state(); anything else belongs to whoever constructed it."
            )
            raise AttributeError(message)
        object.__setattr__(self, name, value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.scope!r})" if self.scope is not None else f"{type(self).__name__}()"

    def _state_changed(self, names: frozenset[str]) -> None:
        """Publish the addresses of the cells that actually moved.

        The bus is the package's one cross-mount refresh mechanism, and a namespace keeps it
        that way: no subscriber index of its own, no payloads, just addresses whose readers
        re-read. Coalescing and delivery are the bus's, already tested.
        """
        slots = type(self)._slots
        self.bus.publish(*(slots[name].address(self) for name in names if name in slots))

    def _state_rolled_back(self) -> None:
        """Nothing to undo: a shared write stages, so a rolled-back one was never published."""


def describe(address: Address) -> str:
    """One address as ``Preferences(Member(1, 2)).theme`` or ``build:123``, for diagnostics.

    Devtools and host logs get a readable name without knowing how an address is built.
    """
    match address:
        case CellAddress(owner=Shared() as owner, name=name):
            return f"{owner!r}.{name}"
        case _:
            return str(address)


__all__ = ["Shared", "describe"]

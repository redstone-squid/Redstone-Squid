"""View state that outlives one mount.

A shared namespace holds what the view owns. It is a class, declared the way a component
declares its own state, and two panels converge on one because something handed them the
same object -- there is no store, no registry and no keyed lookup. What a namespace adds
over an application service is that its writes join the action's transaction and publish
their exact cell addresses without the author writing either half.

Anything the application would still want if no one were looking at it belongs to the
application's data layer, not here.
"""

import logging
from collections.abc import Callable
from typing import Any, ClassVar

from squid_reactivity.core import StateOwner, _Computed, _State
from squid_reactivity.topics import Address, CellAddress, TopicBus

_RESERVED = frozenset({"bus", "scope"})
"""Attribute names a namespace owns, so state may not take one; every underscored name is reserved too."""

_NO_SCOPE: Any = None
"""The scope of a namespace with nothing to say about itself, i.e. ``SharedState[None]``."""

_logger = logging.getLogger(__name__)


def _check_name(cls: type, name: str) -> None:
    if name in _RESERVED or name.startswith("_"):
        message = f"{cls.__name__}.{name}: a namespace reserves {name!r} and every underscored name"
        raise TypeError(message)


class SharedState[ScopeT = None](StateOwner):
    """Base class for a namespace of view state that several mounts share.

    Subclass it, declare fields with `state()`, and hand the instance to whoever should see the
    same values. The handle is the state: it lives exactly as long as the object does, and its
    writes join the current action transaction and publish their `CellAddress` on `bus` at commit.

    `ScopeT` is a label for diagnostics only; nothing requires it to be frozen, hashable or
    validated.

    Raises:
        TypeError: At subclass definition, when a field takes a reserved or underscored name, or
            declares `persist=True` (a namespace is never persisted).
        AttributeError: On assigning an undeclared attribute, or deleting a declared field
            (assign it to reset it; readers elsewhere hold its address).

    Args:
        scope: What this namespace is about, for conflict messages, history labels and devtools.
    """

    _state_slots: ClassVar[dict[str, _State]] = {}
    """Declared state by storage name, which is what a commit reports changed."""
    _resource_names: ClassVar[frozenset[str]] = frozenset()
    """Declared resources by public name. Addressed like state, but loaded rather than written."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        declared: dict[str, _State] = {}
        loaded: set[str] = set()
        for klass in reversed(cls.__mro__):
            for name, descriptor in vars(klass).items():
                if getattr(descriptor, "_reactive_resource_descriptor", False):
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
        cls._state_descriptors = declared
        cls._state_names = frozenset(declared)
        cls._state_slots = {descriptor._name: descriptor for descriptor in declared.values()}
        cls._resource_names = frozenset(loaded)

    def _state_binding(self, name: str) -> CellAddress:
        """Address a field declared here, so a write publishes instead of staying local.

        The one hook that distinguishes `state()` on a namespace from `state()` on a component,
        which has no address.
        """
        return CellAddress(self, name)

    def _resource_binding(self, name: str) -> tuple[CellAddress, Callable[[Any], None]]:
        """Address a resource declared here and hand it `bus.publish`, so a reload re-reads every mount."""
        return CellAddress(self, name), self.bus.publish

    def __init__(self, bus: TopicBus, scope: ScopeT = _NO_SCOPE) -> None:
        self.bus = bus
        self.scope = scope
        self._commit_listeners: set[Callable[[], None]] = set()
        # Eagerly, so every state field carries its address from birth. Its storage outlives
        # every value it holds and is never replaced, so this is the only place one has to be made.
        for descriptor in type(self)._state_descriptors.values():
            descriptor.cell(self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name not in type(self)._state_descriptors and name not in _RESERVED and not name.startswith("_"):
            message = (
                f"{type(self).__name__}.{name} is not declared state. A namespace holds view state "
                f"declared with state(); anything else belongs to whoever constructed it."
            )
            raise AttributeError(message)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        """Raise `AttributeError` for declared state; a namespace field is reset by assigning it.

        Readers elsewhere hold a `CellAddress` for the field, so removal would strand them on an
        empty slot whose next read resurrects the default as though someone had written it.
        """
        if name in type(self)._state_descriptors:
            message = (
                f"{type(self).__name__}.{name} cannot be deleted. Assign it to reset it; a "
                f"namespace field is addressed, and removal would strand readers holding it."
            )
            raise AttributeError(message)
        super().__delattr__(name)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.scope!r})" if self.scope is not None else f"{type(self).__name__}()"

    def _state_changed(self, names: frozenset[str]) -> None:
        """Publish the addresses of the state fields that moved, then run the commit listeners.

        Addresses only, no payloads: readers re-read, and the bus coalesces and delivers. A
        listener that raises is logged and does not stop the others.
        """
        slots = type(self)._state_slots
        self.bus.publish(*(slots[name].address(self) for name in names if name in slots))
        for listener in tuple(self._commit_listeners):
            try:
                listener()
            except Exception:
                _logger.exception("a shared-state commit listener failed")

    def _state_rolled_back(self) -> None:
        """Nothing to undo: a shared write stages, so a rolled-back one was never published."""

    def _add_commit_listener(self, listener: Callable[[], None]) -> None:
        """Register a synchronous observer called after this namespace's changed addresses are published."""
        self._commit_listeners.add(listener)

    def _remove_commit_listener(self, listener: Callable[[], None]) -> None:
        self._commit_listeners.discard(listener)


def describe(address: Address) -> str:
    """One address as ``Preferences(Member(1, 2)).theme`` or ``build:123``, for diagnostics.

    Lowers to `str(address)`; every address kind spells itself.
    """
    return str(address)


__all__ = ["SharedState", "describe"]

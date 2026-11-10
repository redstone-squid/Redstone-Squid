"""View state shared by several mounted components.

Pass the same SharedState instance to components that should share values. Writes
join the current action and publish the changed field addresses on the topic bus.
The instance stays alive while its consumers or host retain it. Store durable
application data in the application data layer.
"""

import logging
from collections.abc import Callable
from typing import Any, ClassVar, override

from squid_reactivity.core import StateOwner, _Computed, _State
from squid_reactivity.topics import Address, CellAddress, TopicBus

_RESERVED = frozenset({"bus", "scope"})
"""Names reserved for namespace metadata. Underscored names are also reserved."""

_NO_SCOPE: Any = None
"""Default label for a namespace without a scope."""

_logger = logging.getLogger(__name__)


def _check_name(cls: type, name: str) -> None:
    if name in _RESERVED or name.startswith("_"):
        message = f"{cls.__name__}.{name}: a namespace reserves {name!r} and every underscored name"
        raise TypeError(message)


class SharedState[ScopeT = None](StateOwner):
    """A group of reactive fields shared by mounted components.

    Declare fields with ``state()`` and pass the same instance to each consumer.
    A host can retain the instance across panel openings, or let it be collected
    when the last consumer releases it.

    The scope labels the instance in diagnostics. It need not be hashable or
    immutable unless the host uses it as a key, for example in SharedStatePool.

    Args:
        bus: Topic bus used to announce changed fields and resources.
        scope: Label identifying the state in logs and conflict reports.
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
        """Give an ordinary state() field an address so its writes reach other consumers.

        StateOwner calls this hook when creating a cell. Local component state has no
        address; shared state uses the same descriptor but publishes through its bus.
        """
        return CellAddress(self, name)

    def _resource_binding(self, name: str) -> tuple[CellAddress, Callable[[Address], None]]:
        """Return the resource address and the callback that publishes its changes.

        Publishing lets every mount that read the resource request a fresh render.
        """
        return CellAddress(self, name), self.bus.publish

    def __init__(self, bus: TopicBus, scope: ScopeT = _NO_SCOPE) -> None:
        self.bus = bus
        self.scope = scope
        self._commit_listeners: set[Callable[[], None]] = set()
        # Create stable cells and addresses before readers can subscribe to this namespace.
        for descriptor in type(self)._state_descriptors.values():
            descriptor.cell(self)

    @override
    def __setattr__(self, name: str, value: Any) -> None:
        if name not in type(self)._state_descriptors and name not in _RESERVED and not name.startswith("_"):
            message = (
                f"{type(self).__name__}.{name} is not declared state. A namespace holds view state "
                f"declared with state(); anything else belongs to whoever constructed it."
            )
            raise AttributeError(message)
        super().__setattr__(name, value)

    @override
    def __delattr__(self, name: str) -> None:
        """Reject deletion of declared state. Assign a new value to reset a field.

        Other consumers retain the field's address. Deleting its value would leave
        those readers referring to an empty cell that silently recreates its default.
        """
        if name in type(self)._state_descriptors:
            message = (
                f"{type(self).__name__}.{name} cannot be deleted. Assign it to reset it; a "
                f"namespace field is addressed, and removal would strand readers holding it."
            )
            raise AttributeError(message)
        super().__delattr__(name)

    @override
    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.scope!r})" if self.scope is not None else f"{type(self).__name__}()"

    @override
    def _state_changed(self, names: frozenset[str]) -> None:
        """Publish changed field addresses, then call the commit listeners.

        Listener failures are logged so later listeners can still run.
        """
        slots = type(self)._state_slots
        self.bus.publish(*(slots[name].address(self) for name in names if name in slots))
        for listener in tuple(self._commit_listeners):
            try:
                listener()
            except Exception:
                _logger.exception("a shared-state commit listener failed")

    @override
    def _state_rolled_back(self) -> None:
        """Do nothing: staged writes were never published."""

    def _add_commit_listener(self, listener: Callable[[], None]) -> None:
        """Register a synchronous observer called after this namespace commits state."""
        self._commit_listeners.add(listener)

    def _remove_commit_listener(self, listener: Callable[[], None]) -> None:
        """Remove a previously registered commit observer."""
        self._commit_listeners.discard(listener)


__all__ = ["SharedState"]

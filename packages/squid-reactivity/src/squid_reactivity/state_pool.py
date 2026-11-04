"""Keep one shared-state instance per scope.

A pool retains its instances until ``delete()`` or ``clear()`` removes them, or
until the pool itself is released. Hosts choose the lifetime by retaining the
pool on a process, session, or request. Each pool accepts one namespace class.
"""

from collections.abc import Callable, Hashable, Mapping
from types import MappingProxyType
from typing import Any, cast, overload, override

from squid_reactivity.shared_state import SharedState
from squid_reactivity.topics import TopicBus

type SharedStateFactory[ScopeT, SharedT] = Callable[[TopicBus, ScopeT], SharedT]
"""How a pool builds a namespace it does not already hold: the pool's bus, and the missing scope."""


class SharedStatePool[ScopeT: Hashable, SharedT: SharedState[Any]]:
    """Cache one shared-state instance for each hashable scope.

    For example, ``pool.get(member_id)`` returns the same instance on each call
    until that scope is deleted. Consumers may continue to use an instance after
    it leaves the pool; deleting it does not reset its state or disconnect its bus.

    The callable constructor overload lets type checkers infer ScopeT directly from
    SharedState[ScopeT]. Pyrefly 1.2 could not infer it through the SharedT bound; see
    docs/plans/squid-ui-redesign/spikes/59/ for the original comparison.

    Args:
        namespace: SharedState subclass to construct. This must be a class, even
            though the constructor overload describes it as a callable for inference.
        bus: Topic bus shared by every instance in the pool.
        factory: Constructor for namespaces that need more than ``(bus, scope)``.
            Use an annotated function so type checkers can infer the scope type.
    """

    @overload
    def __init__(
        self,
        namespace: Callable[[TopicBus, ScopeT], SharedT],
        bus: TopicBus,
        *,
        factory: None = None,
    ) -> None: ...

    @overload
    def __init__(
        self,
        namespace: type[SharedT],
        bus: TopicBus,
        *,
        factory: SharedStateFactory[ScopeT, SharedT],
    ) -> None: ...

    def __init__(
        self,
        namespace: Callable[[TopicBus, ScopeT], SharedT] | type[SharedT],
        bus: TopicBus,
        *,
        factory: SharedStateFactory[ScopeT, SharedT] | None = None,
    ) -> None:
        if not isinstance(namespace, type) or not issubclass(namespace, SharedState):
            message = (
                f"a shared pool owns one SharedState subclass, not {namespace!r}. Pass the namespace "
                f"class; anything it needs beyond (bus, scope) goes in factory=."
            )
            raise TypeError(message)
        self.namespace = cast(type[SharedT], namespace)
        self.bus = bus
        self._factory = factory
        self._handles: dict[ScopeT, SharedT] = {}
        self._constructing: set[ScopeT] = set()

    def get(self, scope: ScopeT) -> SharedT:
        """Return the cached instance for ``scope``, constructing it if absent.

        Construction is synchronous, so asyncio tasks cannot interleave this lookup.
        A factory failure leaves no cached entry. Recursive construction of the same
        scope raises RuntimeError.
        """
        existing = self._handles.get(scope)
        if existing is not None:
            return existing
        return self._adopt(scope, self._create(scope))

    def get_existing(self, scope: ScopeT) -> SharedT | None:
        """Return the canonical namespace for `scope`, or None. Never calls the factory."""
        return self._handles.get(scope)

    def delete(self, scope: ScopeT) -> SharedT | None:
        """Remove a scope and return its cached instance, or None if absent.

        Existing consumers can still read and write the removed instance. A later
        ``get(scope)`` creates another instance. Hosts that require a single shared
        instance must release its consumers before deleting the scope.
        """
        return self._handles.pop(scope, None)

    def clear(self) -> None:
        """Remove all cached instances without changing the instances themselves."""
        self._handles.clear()

    def active(self) -> Mapping[ScopeT, SharedT]:
        """Return a read-only copy of the scope-to-instance mapping.

        Later pool changes do not affect this mapping. It holds strong references
        to the instances, so retaining it also extends their lifetimes.
        """
        return MappingProxyType(dict(self._handles))

    def _create(self, scope: ScopeT) -> SharedT:
        """Construct and validate an instance without caching it.

        The factory must return the configured namespace type with this pool's bus
        and the requested scope. Hosts can prepare the result before calling _adopt.
        """
        if scope in self._constructing:
            message = (
                f"{self.namespace.__name__} for scope {scope!r} is already being constructed. A "
                f"factory may build another scope or use another pool, but not ask for this one."
            )
            raise RuntimeError(message)
        self._constructing.add(scope)
        try:
            created = (self._factory or self.namespace)(self.bus, scope)
        finally:
            self._constructing.discard(scope)
        return self._validated(created, scope)

    def _adopt(self, scope: ScopeT, handle: SharedT) -> SharedT:
        """Cache ``handle`` unless another instance already occupies ``scope``.

        Return the cached instance in either case. A caller that awaits between
        _create and _adopt must use this return value: another caller may have
        filled the cache while it was waiting.
        """
        incumbent = self._handles.get(scope)
        if incumbent is not None:
            return incumbent
        self._handles[scope] = handle
        return handle

    def _validated(self, created: object, scope: ScopeT) -> SharedT:
        if not isinstance(created, self.namespace):
            message = f"namespace factory returned {type(created).__name__}, not {self.namespace.__name__}"
            raise TypeError(message)
        if created.bus is not self.bus:
            message = "namespace factory returned a namespace on another TopicBus"
            raise TypeError(message)
        if created.scope != scope:
            message = "namespace factory returned a namespace for another scope"
            raise TypeError(message)
        return created

    @override
    def __repr__(self) -> str:
        return f"SharedStatePool({self.namespace.__name__}, {len(self._handles)} active)"


__all__ = ["SharedStateFactory", "SharedStatePool"]

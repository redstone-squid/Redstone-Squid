"""An optional keyed lifetime owner for shared view state.

A pool is strong and single-typed: it owns one namespace class and retains one canonical handle per
hashable scope until that scope is deleted, the pool is cleared, or the pool itself is released. There
is no global pool and no lookup by type; a handle lives exactly as long as whoever holds it.
"""

from collections.abc import Callable, Hashable, Mapping
from types import MappingProxyType
from typing import Any, cast, overload

from squid_reactivity.shared_state import SharedState
from squid_reactivity.topics import TopicBus

type SharedStateFactory[ScopeT, SharedT] = Callable[[TopicBus, ScopeT], SharedT]
"""How a pool builds a namespace it does not already hold: the pool's bus, and the missing scope."""


class SharedStatePool[ScopeT: Hashable, SharedT: SharedState[Any]]:
    """Retain one canonical namespace per scope, for as long as this pool is held.

    Put it on the object whose lifetime the handles should share: the bot, a cog, a session. Handles
    constructed outside a pool are unaffected, and their scopes may still be mutable or unhashable.

    Raises `TypeError` at construction when `namespace` is not a `SharedState` subclass: the identity
    check on what the factory returns needs a class to check against.

    Args:
        namespace: The one `SharedState` subclass this pool owns.
        bus: Every handle this pool retains must hold exactly this bus.
        factory: Builds a namespace that needs more than `(bus, scope)`. Annotate it as a function
            rather than passing a lambda: a lambda takes its parameter types from the expected type,
            which still contains the unsolved scope, so it infers as unknown.
    """

    # `namespace` is overloaded as both a class and the callable that class already is. The callable
    # spelling lets a checker read `ScopeT` off `SharedState[ScopeT]` instead of solving it from a
    # type-parameter bound, which Pyrefly 1.2 will not do (docs/plans/squid-ui-redesign/spikes/59/).
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
        """Return the canonical namespace for `scope`, constructing it on the first ask.

        Synchronous end to end, so two tasks cannot both construct. A factory that raises leaves no
        entry behind.

        Raises:
            RuntimeError: The factory asked this pool for the scope it is already building.
            TypeError: The factory returned something other than a `namespace` instance on this bus
                for this scope.
        """
        existing = self._handles.get(scope)
        if existing is not None:
            return existing
        return self._adopt(scope, self._create(scope))

    def get_existing(self, scope: ScopeT) -> SharedT | None:
        """Return the canonical namespace for `scope`, or None. Never calls the factory."""
        return self._handles.get(scope)

    def delete(self, scope: ScopeT) -> SharedT | None:
        """Retire `scope`, returning the handle that was canonical, or None if it was absent.

        The retired handle is not invalidated: holders keep reading and writing it and its writes
        still reach the bus. Only a later `get(scope)` changes, building a second handle. Callers that
        cannot tolerate two live handles for one scope must coordinate their consumers first.
        """
        return self._handles.pop(scope, None)

    def clear(self) -> None:
        """Retire every scope on the same terms as `delete`; no cleanup hook runs, a namespace has none."""
        self._handles.clear()

    def active(self) -> Mapping[ScopeT, SharedT]:
        """A read-only copy of the retained handles, safe to iterate while retiring the scopes it names."""
        return MappingProxyType(dict(self._handles))

    def _create(self, scope: ScopeT) -> SharedT:
        """Build and validate a handle for `scope` without retaining it; the pool is unchanged either way.

        Used with `_adopt` by an owner that must prepare a handle, say hydrate it from a store, before
        it becomes canonical.
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
        """Retain `handle` as canonical for `scope`, or return the incumbent if one already exists.

        An owner that awaited between `_create` and here compares the result's identity to `handle`
        to learn whether it won.
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

    def __repr__(self) -> str:
        return f"SharedStatePool({self.namespace.__name__}, {len(self._handles)} active)"


__all__ = ["SharedStateFactory", "SharedStatePool"]

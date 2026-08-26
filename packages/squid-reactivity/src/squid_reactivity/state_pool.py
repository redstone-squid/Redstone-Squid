"""An optional keyed lifetime owner for shared view state.

:mod:`squid_reactivity.shared_state` argues that there is no store, no registry and no keyed lookup, and
that stays true: a handle is still the state, and it still lives exactly as long as whoever holds
it. This module is the one thing a host may reach for when what it holds is *one handle per scope* —
the `setdefault` cache otherwise written by hand around every namespace.

A pool is strong and single-typed. It owns one namespace class and retains one canonical handle per
hashable scope until that scope is dropped, the pool is cleared, or the pool itself is released.
There is no global pool, no lookup by type, and no way to reach a namespace nobody handed you.
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

    The pool is where a host writes down a lifetime it would otherwise write down in a dict: put it
    on the bot for process lifetime, on a cog for extension lifetime, on a session or a request for
    theirs. Nothing about :class:`~squid_reactivity.shared_state.SharedState` changes -- constructing and passing
    handles directly remains supported, and a scope used outside a pool may still be mutable or
    unhashable.

    ``namespace`` is declared twice in the overloads, as a class and as the constructor that class
    already is. The callable spelling is what lets a type checker read ``ScopeT`` off
    ``SharedState[ScopeT]`` rather than having to solve it from a type-parameter bound, which Pyrefly 1.2
    will not do (see ``docs/plans/squid-ui-redesign/spikes/59/``). Only a namespace *class* is
    accepted at runtime; a bare function is refused at construction, because the identity check a
    pool performs on what its factory returns needs a class to check against.

    Args:
        namespace: The one :class:`~squid_reactivity.shared_state.SharedState` subclass this pool owns.
        bus: The host's topic bus. Every handle this pool retains must hold exactly this bus.
        factory: How to build a namespace that needs more than ``(bus, scope)``. Annotate it as a
            function rather than passing a lambda: a lambda takes its parameter types from the
            expected type, which still contains the unsolved scope, so it infers as unknown.
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
        """Return the canonical namespace for `scope`, constructing it on the first ask.

        One synchronous lookup: there is no await between the miss and the insertion, so two
        asyncio tasks cannot both construct. A factory that raises leaves no entry behind.
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

        The retired handle is not invalidated or mutated: components still holding it keep reading
        and writing the same state, and its writes still reach the bus. What changes is only that a
        later `get(scope)` builds a **new generation** rather than returning this one. Callers that
        cannot tolerate two generations being live at once must coordinate their consumers before
        dropping -- the pool does not know who is holding what.
        """
        return self._handles.pop(scope, None)

    def clear(self) -> None:
        """Retire every scope, on the same terms as `drop`.

        No cleanup hook runs, because a namespace has none; adding one here would make a pooled
        handle behave differently from one constructed directly, which is the whole thing this
        module is trying not to do.
        """
        self._handles.clear()

    def active(self) -> Mapping[ScopeT, SharedT]:
        """Snapshot the retained handles, copied at the moment of the call.

        The result is not a view: mutating the pool afterwards -- get, drop, clear -- leaves a
        snapshot already returned unchanged, so a caller may iterate it while retiring the very
        scopes it names. It is read-only at runtime as well as statically, holds strong references
        to the handles it names, and never invokes the factory.
        """
        return MappingProxyType(dict(self._handles))

    def _create(self, scope: ScopeT) -> SharedT:
        """Build and validate a handle for `scope` without retaining it.

        Runs the factory under the same-scope reentrancy guard and applies the three validations.
        The pool is unchanged whether this returns or raises. Exposed for an owner that must
        prepare a handle -- hydrate it from a store, say -- before it becomes canonical.
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

        Never calls the factory. Returns whichever handle is canonical afterwards; a caller that
        released control between `_create` and here compares identity to learn whether it won, which
        is how an owner with an await in the middle avoids publishing a second canonical handle.
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

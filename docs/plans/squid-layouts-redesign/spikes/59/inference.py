"""Spike: can Pyrefly infer `ScopeT` from a namespace's base class?

Evidence for [59](../../59-shared-pool.md), not a staging area -- nothing here is meant to be
promoted into a package. See README.md for the recorded output and what it decided.

Run:

    uv run --locked pyrefly check docs/plans/squid-layouts-redesign/spikes/59/inference.py

`docs/` is outside `[tool.pyrefly] project-includes`, so this file is only ever checked by hand.
"""

from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any, assert_type, overload, reveal_type

from squid_reactivity import Shared, TopicBus

# --------------------------------------------------------------------------------------
# Scopes, declared here rather than imported: the question is about `squid_reactivity`, and
# `squid_layouts.discord.sessions` would drag discord.py in for two frozen dataclasses.
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UserScope:
    user_id: int


@dataclass(frozen=True, slots=True)
class UserGuildScope:
    user_id: int
    guild_id: int


class Preferences(Shared[UserGuildScope]):
    """The plain case: `__init__` is inherited unchanged."""


class SearchState(Shared[UserGuildScope]):
    """The extra-collaborator case, which is why `factory=` exists at all."""

    def __init__(self, bus: TopicBus, scope: UserGuildScope, *, index: object) -> None:
        super().__init__(bus, scope)
        self._index = index


class Anonymous(Shared):
    """No scope argument, so `ScopeT` falls to its PEP 696 default of `None`."""


# --------------------------------------------------------------------------------------
# Variant A -- the plan as written. `ScopeT` occurs in no parameter type, only in the bound.
# --------------------------------------------------------------------------------------


class PoolA[ScopeT: Hashable, SharedT: Shared[ScopeT]]:
    def __init__(
        self,
        namespace: type[SharedT],
        bus: TopicBus,
        *,
        factory: Callable[[TopicBus, ScopeT], SharedT] | None = None,
    ) -> None: ...

    def get(self, scope: ScopeT) -> SharedT: ...

    def active(self) -> Mapping[ScopeT, SharedT]: ...


def variant_a(bus: TopicBus) -> None:
    pool = PoolA(Preferences, bus)
    reveal_type(pool)
    # The whole pool type, not just the handle: an inferred `PoolA[Any, Preferences]` would
    # pass a handle-only assertion while silently accepting the wrong scope below.
    assert_type(pool, PoolA[UserGuildScope, Preferences])
    assert_type(pool.get(UserGuildScope(1, 2)), Preferences)


def variant_a_negative(bus: TopicBus) -> None:
    """A `UserScope` handed to a `UserGuildScope` pool must be an error, not a runtime miss."""
    pool = PoolA(Preferences, bus)
    pool.get(UserScope(1))


def variant_a_factory(bus: TopicBus, index: object) -> None:
    lambda_pool = PoolA(SearchState, bus, factory=lambda bus, scope: SearchState(bus, scope, index=index))
    reveal_type(lambda_pool)

    def make(bus: TopicBus, scope: UserGuildScope) -> SearchState:
        return SearchState(bus, scope, index=index)

    annotated_pool = PoolA(SearchState, bus, factory=make)
    reveal_type(annotated_pool)


def variant_a_control(bus: TopicBus) -> None:
    """The explicit escape hatch, which must work whatever inference does."""
    pool = PoolA[UserGuildScope, Preferences](Preferences, bus)
    assert_type(pool, PoolA[UserGuildScope, Preferences])


def variant_a_unscoped(bus: TopicBus) -> None:
    """Does `Shared`'s PEP 696 `ScopeT = None` default participate?"""
    pool = PoolA(Anonymous, bus)
    reveal_type(pool)


# --------------------------------------------------------------------------------------
# Variant B -- `namespace` typed as the constructor it already is, so `ScopeT` is solved from
# a callback parameter position instead of from a bound.
# --------------------------------------------------------------------------------------


class PoolB[ScopeT: Hashable, SharedT: Shared[ScopeT]]:
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
        factory: Callable[[TopicBus, ScopeT], SharedT],
    ) -> None: ...

    def __init__(
        self,
        namespace: Callable[[TopicBus, ScopeT], SharedT] | type[SharedT],
        bus: TopicBus,
        *,
        factory: Callable[[TopicBus, ScopeT], SharedT] | None = None,
    ) -> None: ...

    def get(self, scope: ScopeT) -> SharedT: ...


def variant_b(bus: TopicBus) -> None:
    pool = PoolB(Preferences, bus)
    reveal_type(pool)
    assert_type(pool, PoolB[UserGuildScope, Preferences])
    assert_type(pool.get(UserGuildScope(1, 2)), Preferences)


def variant_b_negative(bus: TopicBus) -> None:
    pool = PoolB(Preferences, bus)
    pool.get(UserScope(1))


def variant_b_factory(bus: TopicBus, index: object) -> None:
    lambda_pool = PoolB(SearchState, bus, factory=lambda bus, scope: SearchState(bus, scope, index=index))
    reveal_type(lambda_pool)

    def make(bus: TopicBus, scope: UserGuildScope) -> SearchState:
        return SearchState(bus, scope, index=index)

    annotated_pool = PoolB(SearchState, bus, factory=make)
    reveal_type(annotated_pool)


# --------------------------------------------------------------------------------------
# Variance probe -- this is what separates "loud error" from "silently Any" when a solver
# falls back to the declared bound.
# --------------------------------------------------------------------------------------


def variance(scoped: Shared[UserGuildScope]) -> None:
    as_hashable: Shared[Hashable] = scoped
    as_any: Shared[Any] = scoped
    reveal_type(as_hashable)
    reveal_type(as_any)


# --------------------------------------------------------------------------------------
# Variant C -- variant B without the dependent bound, which Pyrefly 1.2 rejects outright on
# both A and B ("Type variable bounds and constraints must be concrete"). The overloaded
# constructor already ties `ScopeT` to `SharedT`, so the bound was decoration.
# --------------------------------------------------------------------------------------


class PoolC[ScopeT: Hashable, SharedT: Shared[Any]]:
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
        factory: Callable[[TopicBus, ScopeT], SharedT],
    ) -> None: ...

    def __init__(
        self,
        namespace: Callable[[TopicBus, ScopeT], SharedT] | type[SharedT],
        bus: TopicBus,
        *,
        factory: Callable[[TopicBus, ScopeT], SharedT] | None = None,
    ) -> None: ...

    def get(self, scope: ScopeT) -> SharedT: ...

    def get_existing(self, scope: ScopeT) -> SharedT | None: ...

    def active(self) -> Mapping[ScopeT, SharedT]: ...


def variant_c(bus: TopicBus) -> None:
    pool = PoolC(Preferences, bus)
    reveal_type(pool)
    assert_type(pool, PoolC[UserGuildScope, Preferences])
    assert_type(pool.get(UserGuildScope(1, 2)), Preferences)
    assert_type(pool.get_existing(UserGuildScope(1, 2)), Preferences | None)
    assert_type(pool.active(), Mapping[UserGuildScope, Preferences])


def variant_c_negative(bus: TopicBus) -> None:
    pool = PoolC(Preferences, bus)
    pool.get(UserScope(1))


def variant_c_factory(bus: TopicBus, index: object) -> None:
    def make(bus: TopicBus, scope: UserGuildScope) -> SearchState:
        return SearchState(bus, scope, index=index)

    annotated_pool = PoolC(SearchState, bus, factory=make)
    reveal_type(annotated_pool)
    assert_type(annotated_pool, PoolC[UserGuildScope, SearchState])


def variant_c_unscoped(bus: TopicBus) -> None:
    pool = PoolC(Anonymous, bus)
    reveal_type(pool)


def variant_c_rejects_non_namespace(bus: TopicBus) -> None:
    """`SharedT: Shared[Any]` must still refuse something that is not a namespace at all."""

    class NotANamespace:
        def __init__(self, bus: TopicBus, scope: UserGuildScope) -> None: ...

    PoolC(NotANamespace, bus)

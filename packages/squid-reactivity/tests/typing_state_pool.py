"""Pins `SharedStatePool`'s inference under `just typecheck`; nothing here runs.

The pool's whole claim is that the namespace's `SharedState[ScopeT]`, the lookup scope, the factory
argument and the returned handle are one inferred pair. The assertions below are on the **full**
pool type rather than on the handle alone, because the failure mode this guards against is
`SharedStatePool[Any, Preferences]` -- which would satisfy a handle-only assertion while silently
accepting the wrong scope. See `docs/plans/squid-ui-redesign/spikes/59/` for the measurement
that chose this signature.
"""

from dataclasses import dataclass
from typing import assert_type

from squid_reactivity import LocalTopicBus, SharedState, SharedStatePool, TopicBus, state


@dataclass(frozen=True, slots=True)
class UserScope:
    user_id: int


@dataclass(frozen=True, slots=True)
class UserGuildScope:
    user_id: int
    guild_id: int


class Preferences(SharedState[UserGuildScope]):
    theme: str = state("dark")


class SearchState(SharedState[UserGuildScope]):
    def __init__(self, bus: TopicBus, scope: UserGuildScope, *, index: object) -> None:
        super().__init__(bus, scope)
        self._index = index


class Anonymous(SharedState):
    note: str = state("")


bus = LocalTopicBus()
index = object()

# The scope comes off the base class, with nothing else in the call to pin it.
preferences = SharedStatePool(Preferences, bus)
assert_type(preferences, SharedStatePool[UserGuildScope, Preferences])
assert_type(preferences.get(UserGuildScope(1, 2)), Preferences)
assert_type(preferences.get_existing(UserGuildScope(1, 2)), Preferences | None)
assert_type(preferences.delete(UserGuildScope(1, 2)), Preferences | None)

# A `UserScope` handed to a user-guild pool is a type error, not a runtime miss. This is the
# assertion the whole signature exists for; if the ignore below ever goes unused, inference has
# regressed to `Any` and the pin has stopped meaning anything.
preferences.get(UserScope(1))  # pyrefly: ignore[bad-argument-type]

# An annotated factory carries the scope just as well as the class does. A bare lambda does not:
# it takes its parameter types from the expected type, which still holds the unsolved scope.


def make_search_state(pool_bus: TopicBus, scope: UserGuildScope) -> SearchState:
    return SearchState(pool_bus, scope, index=index)


searches = SharedStatePool(SearchState, bus, factory=make_search_state)
assert_type(searches, SharedStatePool[UserGuildScope, SearchState])
assert_type(searches.get(UserGuildScope(1, 2)), SearchState)

# An unparameterised namespace is `SharedState[None]`, and its pool keys on `None`.
anonymous = SharedStatePool(Anonymous, bus)
assert_type(anonymous, SharedStatePool[None, Anonymous])
assert_type(anonymous.get(None), Anonymous)

# Explicit parameterisation stays available as the escape hatch.
explicit = SharedStatePool[UserGuildScope, Preferences](Preferences, bus)
assert_type(explicit, SharedStatePool[UserGuildScope, Preferences])

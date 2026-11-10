"""Check ReactivePool scope and instance inference with the project type checker.

Assert the full pool type: checking only the returned instance would miss a
regression to ReactivePool[Any, Preferences], which accepts invalid scopes.
This file is checked statically and is not a pytest module.
The inference experiments behind this signature are in
docs/plans/completed/squid-layouts-redesign/spikes/59/.
"""

from dataclasses import dataclass
from typing import assert_type

from squid_reactivity import Reactive, ReactivePool, state


@dataclass(frozen=True, slots=True)
class UserScope:
    user_id: int


@dataclass(frozen=True, slots=True)
class UserGuildScope:
    user_id: int
    guild_id: int


class Preferences(Reactive):
    theme: str = state("dark")


def make_preferences(key: UserGuildScope) -> Preferences:
    return Preferences()


class SearchState(Reactive):
    def __init__(self, *, index: object) -> None:
        self._index = index


class Anonymous(Reactive):
    note: str = state("")


index = object()

# The key and value types come from the factory signature.
preferences = ReactivePool(make_preferences)
assert_type(preferences, ReactivePool[UserGuildScope, Preferences])
assert_type(preferences.get(UserGuildScope(1, 2)), Preferences)
assert_type(preferences.get_existing(UserGuildScope(1, 2)), Preferences | None)
assert_type(preferences.delete(UserGuildScope(1, 2)), Preferences | None)

# A `UserScope` handed to a user-guild pool is a type error, not a runtime miss. This is the
# assertion the whole signature exists for; if the ignore below ever goes unused, inference has
# regressed to `Any` and the pin has stopped meaning anything.
preferences.get(UserScope(1))  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]

# An annotated factory carries the scope just as well as the class does. A bare lambda does not:
# it takes its parameter types from the expected type, which still holds the unsolved scope.


def make_search_state(scope: UserGuildScope) -> SearchState:
    return SearchState(index=index)


searches = ReactivePool(make_search_state)
assert_type(searches, ReactivePool[UserGuildScope, SearchState])
assert_type(searches.get(UserGuildScope(1, 2)), SearchState)


def make_anonymous(key: None) -> Anonymous:
    return Anonymous()


anonymous = ReactivePool(make_anonymous)
assert_type(anonymous, ReactivePool[None, Anonymous])
assert_type(anonymous.get(None), Anonymous)

# Explicit parameterisation stays available as the escape hatch.
explicit = ReactivePool[UserGuildScope, Preferences](make_preferences)
assert_type(explicit, ReactivePool[UserGuildScope, Preferences])

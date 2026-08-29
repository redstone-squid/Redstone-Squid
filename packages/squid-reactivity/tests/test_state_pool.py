"""Contracts for the optional keyed lifetime owner."""

from collections.abc import Hashable
from dataclasses import dataclass

import pytest

from squid_reactivity import LocalTopicBus, SharedState, SharedStatePool, TopicBus, state


@dataclass(frozen=True, slots=True)
class UserScope:
    user_id: int


@dataclass(frozen=True, slots=True)
class GuildScope:
    guild_id: int


class Preferences(SharedState[UserScope]):
    theme: str = state("dark")


class SearchState(SharedState[UserScope]):
    query: str = state("")

    def __init__(self, bus: TopicBus, scope: UserScope, *, index: object) -> None:
        super().__init__(bus, scope)
        self._index = index


class Anonymous(SharedState):
    note: str = state("")


@pytest.fixture
def bus() -> LocalTopicBus:
    return LocalTopicBus()


def test_one_canonical_handle_per_equal_scope(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)

    mine = pool.get(UserScope(1))
    same = pool.get(UserScope(1))
    other = pool.get(UserScope(2))

    assert mine is same
    assert mine is not other


def test_the_default_factory_receives_the_pool_bus_and_requested_scope(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)

    preferences = pool.get(UserScope(7))

    assert preferences.bus is bus
    assert preferences.scope == UserScope(7)


def test_a_custom_factory_is_called_once_per_generation_with_its_dependencies(bus: LocalTopicBus) -> None:
    index = object()
    calls: list[UserScope] = []

    def make(pool_bus: TopicBus, scope: UserScope) -> SearchState:
        calls.append(scope)
        return SearchState(pool_bus, scope, index=index)

    pool = SharedStatePool(SearchState, bus, factory=make)

    first = pool.get(UserScope(1))
    pool.get(UserScope(1))

    assert calls == [UserScope(1)]
    assert first._index is index


def test_a_hit_does_not_construct_a_namespace_it_throws_away(bus: LocalTopicBus) -> None:
    """The `setdefault` shape this replaces built one per call and discarded it."""
    built = 0

    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        nonlocal built
        built += 1
        return Preferences(pool_bus, scope)

    pool = SharedStatePool(Preferences, bus, factory=make)

    pool.get(UserScope(1))
    pool.get(UserScope(1))
    pool.get(UserScope(1))

    assert built == 1


def test_a_failing_factory_caches_nothing(bus: LocalTopicBus) -> None:
    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        raise RuntimeError("no")

    pool = SharedStatePool(Preferences, bus, factory=make)

    with pytest.raises(RuntimeError, match="no"):
        pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is None
    assert pool.active() == {}


def test_same_scope_recursive_construction_fails_by_name(bus: LocalTopicBus) -> None:
    pool: SharedStatePool[UserScope, Preferences]

    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        return pool.get(scope)

    pool = SharedStatePool(Preferences, bus, factory=make)

    with pytest.raises(RuntimeError, match="Preferences for scope UserScope"):
        pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is None


def test_a_factory_may_construct_another_scope(bus: LocalTopicBus) -> None:
    pool: SharedStatePool[UserScope, Preferences]

    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        if scope.user_id == 1:
            pool.get(UserScope(2))
        return Preferences(pool_bus, scope)

    pool = SharedStatePool(Preferences, bus, factory=make)

    assert pool.get(UserScope(1)).scope == UserScope(1)
    assert pool.get_existing(UserScope(2)) is not None


def test_a_wrong_namespace_type_is_refused_without_becoming_active(bus: LocalTopicBus) -> None:
    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        return Anonymous(pool_bus)  # pyrefly: ignore[bad-return]

    pool = SharedStatePool(Preferences, bus, factory=make)

    with pytest.raises(TypeError, match="returned Anonymous, not Preferences"):
        pool.get(UserScope(1))
    assert pool.active() == {}


def test_a_namespace_on_another_bus_is_refused(bus: LocalTopicBus) -> None:
    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        return Preferences(LocalTopicBus(), scope)

    pool = SharedStatePool(Preferences, bus, factory=make)

    with pytest.raises(TypeError, match="another TopicBus"):
        pool.get(UserScope(1))


def test_a_namespace_for_another_scope_is_refused(bus: LocalTopicBus) -> None:
    def make(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        return Preferences(pool_bus, UserScope(99))

    pool = SharedStatePool(Preferences, bus, factory=make)

    with pytest.raises(TypeError, match="another scope"):
        pool.get(UserScope(1))


def test_get_existing_distinguishes_a_miss_without_constructing(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)

    assert pool.get_existing(UserScope(1)) is None
    assert pool.active() == {}
    pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is not None


def test_drop_retires_a_handle_that_stays_usable_while_a_new_generation_starts(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    first = pool.get(UserScope(1))

    retired = pool.delete(UserScope(1))
    second = pool.get(UserScope(1))

    assert retired is first
    assert second is not first
    # The retired generation is untouched: still readable, still writable, still reactive.
    seen: list[object] = []
    bus.subscribe(type(first).theme.address(first), seen.append)  # pyrefly: ignore[missing-attribute]
    first.theme = "light"
    assert first.theme == "light"
    assert second.theme == "dark"
    assert seen


def test_dropping_an_absent_scope_returns_none(bus: LocalTopicBus) -> None:
    assert SharedStatePool(Preferences, bus).delete(UserScope(1)) is None


def test_clear_empties_the_pool(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    first = pool.get(UserScope(1))
    pool.get(UserScope(2))

    pool.clear()

    assert pool.active() == {}
    assert pool.get(UserScope(1)) is not first


def test_a_snapshot_is_copied_not_a_view(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    pool.get(UserScope(1))

    snapshot = pool.active()
    pool.get(UserScope(2))
    pool.clear()

    assert set(snapshot) == {UserScope(1)}


def test_a_snapshot_can_be_iterated_while_its_scopes_are_retired(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    for user_id in range(5):
        pool.get(UserScope(user_id))

    for scope in pool.active():
        pool.delete(scope)

    assert pool.active() == {}


def test_a_snapshot_cannot_mutate_the_pool(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    pool.get(UserScope(1))

    snapshot = pool.active()

    with pytest.raises(TypeError):
        snapshot[UserScope(2)] = Preferences(bus, UserScope(2))  # pyrefly: ignore[unsupported-operation]


def test_a_mutable_by_convention_but_hashable_scope_works(bus: LocalTopicBus) -> None:
    class Key:
        """Hashable by identity, which is all a dict asks."""

    class Loose(SharedState[Hashable]):
        pass

    pool: SharedStatePool[Hashable, Loose] = SharedStatePool(Loose, bus)
    key = Key()

    assert pool.get(key) is pool.get(key)


def test_an_unhashable_pool_key_raises_the_normal_type_error(bus: LocalTopicBus) -> None:
    class Loose(SharedState[Hashable]):
        pass

    pool: SharedStatePool[Hashable, Loose] = SharedStatePool(Loose, bus)

    with pytest.raises(TypeError, match="unhashable"):
        pool.get(["guild", 7])  # pyrefly: ignore[bad-argument-type]

    # ... while a direct namespace with the same scope is still fine, per 40's model.
    assert Loose(bus, ["guild", 7]).scope == ["guild", 7]  # pyrefly: ignore[bad-argument-type]


def test_an_unscoped_namespace_pools_on_none(bus: LocalTopicBus) -> None:
    pool: SharedStatePool[None, Anonymous] = SharedStatePool(Anonymous, bus)

    assert pool.get(None) is pool.get(None)


def test_a_pool_needs_a_namespace_class_not_a_function(bus: LocalTopicBus) -> None:
    def looks_right(pool_bus: TopicBus, scope: UserScope) -> Preferences:
        return Preferences(pool_bus, scope)

    with pytest.raises(TypeError, match="owns one SharedState subclass"):
        SharedStatePool(looks_right, bus)


def test_two_pools_over_one_namespace_do_not_converge(bus: LocalTopicBus) -> None:
    """A pool is a lifetime owner, not a registry: nothing is global."""
    left = SharedStatePool(Preferences, bus)
    right = SharedStatePool(Preferences, bus)

    assert left.get(UserScope(1)) is not right.get(UserScope(1))


def test_repr_names_the_namespace_and_how_many_are_live(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)
    pool.get(UserScope(1))

    assert repr(pool) == "SharedStatePool(Preferences, 1 active)"


def test_a_scope_is_matched_by_equality_not_identity(bus: LocalTopicBus) -> None:
    pool = SharedStatePool(Preferences, bus)

    assert pool.get(UserScope(1)) is pool.get(UserScope(1))
    assert pool.get_existing(GuildScope(1)) is None  # pyrefly: ignore[bad-argument-type]

"""Shared-state caching, scope validation, and instance lifetime."""

from collections.abc import Hashable
from dataclasses import dataclass

import pytest

from squid_reactivity import CellAddress, Reactive, ReactivePool, SubscriptionReconciler, state


@dataclass(frozen=True, slots=True)
class UserScope:
    user_id: int


@dataclass(frozen=True, slots=True)
class GuildScope:
    guild_id: int


class Preferences(Reactive):
    theme: str = state("dark")


def make_preferences(key: UserScope) -> Preferences:
    return Preferences()


class SearchState(Reactive):
    query: str = state("")

    def __init__(self, *, index: object) -> None:
        self._index = index


class Anonymous(Reactive):
    note: str = state("")


def test_one_canonical_handle_per_equal_scope() -> None:
    pool = ReactivePool(make_preferences)

    mine = pool.get(UserScope(1))
    same = pool.get(UserScope(1))
    other = pool.get(UserScope(2))

    assert mine is same
    assert mine is not other


def test_the_factory_receives_the_requested_key() -> None:
    requested: list[UserScope] = []

    def make(key: UserScope) -> Preferences:
        requested.append(key)
        return Preferences()

    pool = ReactivePool(make)
    pool.get(UserScope(7))
    assert requested == [UserScope(7)]


def test_a_custom_factory_is_called_once_per_generation_with_its_dependencies() -> None:
    index = object()
    calls: list[UserScope] = []

    def make(scope: UserScope) -> SearchState:
        calls.append(scope)
        return SearchState(index=index)

    pool = ReactivePool(make)

    first = pool.get(UserScope(1))
    pool.get(UserScope(1))

    assert calls == [UserScope(1)]
    assert first._index is index


def test_a_hit_does_not_construct_a_namespace_it_throws_away() -> None:
    """Avoid eager factory calls such as ``cache.setdefault(scope, factory())``."""
    built = 0

    def make(scope: UserScope) -> Preferences:
        nonlocal built
        built += 1
        return Preferences()

    pool = ReactivePool(make)

    pool.get(UserScope(1))
    pool.get(UserScope(1))
    pool.get(UserScope(1))

    assert built == 1


def test_a_failing_factory_caches_nothing() -> None:
    def make(scope: UserScope) -> Preferences:
        raise RuntimeError("no")

    pool = ReactivePool(make)

    with pytest.raises(RuntimeError, match="no"):
        pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is None
    assert pool.active() == {}


def test_same_scope_recursive_construction_fails_by_name() -> None:
    pool: ReactivePool[UserScope, Preferences]

    def make(scope: UserScope) -> Preferences:
        return pool.get(scope)

    pool = ReactivePool(make)

    with pytest.raises(RuntimeError, match="reactive object for key UserScope"):
        pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is None


def test_a_factory_may_construct_another_scope() -> None:
    pool: ReactivePool[UserScope, Preferences]

    def make(scope: UserScope) -> Preferences:
        if scope.user_id == 1:
            pool.get(UserScope(2))
        return Preferences()

    pool = ReactivePool(make)

    assert isinstance(pool.get(UserScope(1)), Preferences)
    assert pool.get_existing(UserScope(2)) is not None


def test_a_nonreactive_factory_result_is_refused_without_becoming_active() -> None:
    def make(scope: UserScope) -> Preferences:
        return object()  # type: ignore

    pool = ReactivePool(make)
    with pytest.raises(TypeError, match="Reactive"):
        pool.get(UserScope(1))
    assert pool.active() == {}


def test_one_object_cannot_be_adopted_under_two_keys() -> None:
    preferences = Preferences()
    pool = ReactivePool(lambda scope: preferences)
    assert pool.get(UserScope(1)) is preferences
    with pytest.raises(ValueError, match="different keys"):
        pool.get(UserScope(2))
    assert pool.get_existing(UserScope(2)) is None


def test_keys_do_not_require_metadata_on_the_object() -> None:
    pool: ReactivePool[UserScope, Anonymous] = ReactivePool(lambda scope: Anonymous())
    assert pool.get(UserScope(1)) is pool.get(UserScope(1))
    assert pool.get(UserScope(1)) is not pool.get(UserScope(2))


def test_get_existing_distinguishes_a_miss_without_constructing() -> None:
    pool = ReactivePool(make_preferences)

    assert pool.get_existing(UserScope(1)) is None
    assert pool.active() == {}
    pool.get(UserScope(1))
    assert pool.get_existing(UserScope(1)) is not None


def test_drop_retires_a_handle_that_stays_usable_while_a_new_generation_starts() -> None:
    pool = ReactivePool(make_preferences)
    first = pool.get(UserScope(1))

    retired = pool.delete(UserScope(1))
    second = pool.get(UserScope(1))

    assert retired is first
    assert second is not first
    # The retired generation is untouched: still readable, still writable, still reactive.
    seen: list[object] = []
    subscriptions = SubscriptionReconciler(None, seen.append)
    subscriptions.stage([CellAddress(first, "theme")])
    subscriptions.commit()
    first.theme = "light"
    assert first.theme == "light"
    assert second.theme == "dark"
    assert seen


def test_dropping_an_absent_scope_returns_none() -> None:
    assert ReactivePool(make_preferences).delete(UserScope(1)) is None


def test_clear_empties_the_pool() -> None:
    pool = ReactivePool(make_preferences)
    first = pool.get(UserScope(1))
    pool.get(UserScope(2))

    pool.clear()

    assert pool.active() == {}
    assert pool.get(UserScope(1)) is not first


def test_a_snapshot_is_copied_not_a_view() -> None:
    pool = ReactivePool(make_preferences)
    pool.get(UserScope(1))

    snapshot = pool.active()
    pool.get(UserScope(2))
    pool.clear()

    assert set(snapshot) == {UserScope(1)}


def test_a_snapshot_can_be_iterated_while_its_scopes_are_retired() -> None:
    pool = ReactivePool(make_preferences)
    for user_id in range(5):
        pool.get(UserScope(user_id))

    for scope in pool.active():
        pool.delete(scope)

    assert pool.active() == {}


def test_a_snapshot_cannot_mutate_the_pool() -> None:
    pool = ReactivePool(make_preferences)
    pool.get(UserScope(1))

    snapshot = pool.active()

    with pytest.raises(TypeError):
        snapshot[UserScope(2)] = Preferences()  # pyright: ignore[reportIndexIssue]  # pyrefly: ignore[unsupported-operation]


def test_a_mutable_by_convention_but_hashable_scope_works() -> None:
    class Key:
        """Hashable by identity, which is all a dict asks."""

    class Loose(Reactive):
        pass

    pool: ReactivePool[Hashable, Loose] = ReactivePool(lambda key: Loose())
    key = Key()

    assert pool.get(key) is pool.get(key)


def test_an_unhashable_pool_key_raises_the_normal_type_error() -> None:
    class Loose(Reactive):
        pass

    pool: ReactivePool[Hashable, Loose] = ReactivePool(lambda key: Loose())

    with pytest.raises(TypeError, match="unhashable"):
        pool.get(["guild", 7])  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]


def test_an_unscoped_namespace_pools_on_none() -> None:
    pool: ReactivePool[None, Anonymous] = ReactivePool(lambda key: Anonymous())

    assert pool.get(None) is pool.get(None)


def test_two_pools_over_one_namespace_do_not_converge() -> None:
    """Each pool has its own cache, even when the namespace class is shared."""
    left = ReactivePool(make_preferences)
    right = ReactivePool(make_preferences)

    assert left.get(UserScope(1)) is not right.get(UserScope(1))


def test_repr_names_the_namespace_and_how_many_are_live() -> None:
    pool = ReactivePool(make_preferences)
    pool.get(UserScope(1))

    assert repr(pool) == "ReactivePool(1 active)"


def test_a_scope_is_matched_by_equality_not_identity() -> None:
    pool = ReactivePool(make_preferences)

    assert pool.get(UserScope(1)) is pool.get(UserScope(1))
    assert pool.get_existing(GuildScope(1)) is None  # pyright: ignore[reportArgumentType]  # pyrefly: ignore[bad-argument-type]

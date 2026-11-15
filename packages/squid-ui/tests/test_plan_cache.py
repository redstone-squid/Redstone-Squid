"""Planning cache stores share eviction rules but have independent retention."""

from collections.abc import Callable

import pytest

from squid_ui import scene
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.composition import OccurrenceId


@pytest.fixture
def cache() -> PlanCache[scene.ComponentsV2]:
    return PlanCache(capacity=2)


@pytest.fixture
def store(
    cache: PlanCache[scene.ComponentsV2],
) -> tuple[Callable[[str], None], Callable[[str], bool]]:
    return lambda key: cache.remember_partitions(key, {}), lambda key: cache.partition_hint(key) is not None


@pytest.mark.parametrize("refresh", ["read", "replace"])
def test_successful_reads_and_replacements_promote_entries(
    store: tuple[Callable[[str], None], Callable[[str], bool]], refresh: str
) -> None:
    put, contains = store
    put("first")
    put("second")
    if refresh == "read":
        assert contains("first")
    else:
        put("first")
    put("third")
    assert not contains("second")
    assert contains("first")
    assert contains("third")


def test_misses_leave_eviction_order_unchanged(store: tuple[Callable[[str], None], Callable[[str], bool]]) -> None:
    put, contains = store
    put("first")
    put("second")
    assert not contains("missing")
    put("third")
    assert not contains("first")
    assert contains("second")
    assert contains("third")


@pytest.mark.parametrize("capacity", [0, 1, 3])
def test_capacity_changes_apply_on_the_next_write(
    cache: PlanCache[scene.ComponentsV2], store: tuple[Callable[[str], None], Callable[[str], bool]], capacity: int
) -> None:
    put, contains = store
    put("first")
    put("second")
    cache.capacity = capacity
    assert contains("first")
    assert contains("second")
    put("third")
    assert contains("first") is (capacity == 3)
    assert contains("second") is (capacity == 3)
    assert contains("third") is (capacity > 0)


def test_length_counts_retained_partition_hints(cache: PlanCache[scene.ComponentsV2]) -> None:
    cache.remember_partitions("first", {})
    cache.remember_partitions("second", {})
    assert len(cache) == 2
    cache.capacity = 1
    cache.remember_partitions("third", {})
    assert len(cache) == 1
    assert cache.partition_hint("first") is None
    assert cache.partition_hint("third") == {}


def test_partition_hints_are_copied_and_read_only(cache: PlanCache[scene.ComponentsV2]) -> None:
    region = OccurrenceId(("root", "region"))
    cuts = {region: (2, 4)}
    cache.remember_partitions("plan", cuts)
    cuts.clear()
    hint = cache.partition_hint("plan")
    assert hint == {region: (2, 4)}
    with pytest.raises(TypeError):
        hint[region] = (1,)  # type: ignore  # Verify runtime rejection of an invalid write.
    cache.remember_partitions("plan", {region: (3,)})
    assert cache.partition_hint("plan") == {region: (3,)}
    assert hint == {region: (2, 4)}


@pytest.mark.parametrize("capacity", [0, -1])
def test_initial_capacity_must_be_positive(capacity: int) -> None:
    with pytest.raises(ValueError, match="plan cache capacity must be positive"):
        PlanCache(capacity)

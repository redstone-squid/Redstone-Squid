"""Planning cache stores share eviction rules but have independent retention."""

from collections.abc import Callable

import pytest

from squid_ui import scene
from squid_ui.planning.cache import CachedPlan, PlanCache
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.result import PlanReport


@pytest.fixture
def cache() -> PlanCache[scene.ComponentsV2]:
    return PlanCache(capacity=2)


@pytest.fixture
def cached_plan() -> CachedPlan[scene.ComponentsV2]:
    return CachedPlan(
        scene.Scene(scene.Codec.protocol, "discord.components-v2", 1, scene.ComponentsV2(())), PlanReport()
    )


@pytest.fixture(params=["plans", "certifications", "partitions"])
def store(
    request: pytest.FixtureRequest, cache: PlanCache[scene.ComponentsV2], cached_plan: CachedPlan[scene.ComponentsV2]
) -> tuple[Callable[[str], None], Callable[[str], bool]]:
    if request.param == "plans":
        return lambda key: cache.put(key, cached_plan), lambda key: cache.get(key) is not None
    if request.param == "certifications":
        return cache.certify_incremental, cache.admits_incremental
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


def test_stores_have_independent_capacity_and_length_counts_only_plans(
    cache: PlanCache[scene.ComponentsV2], cached_plan: CachedPlan[scene.ComponentsV2]
) -> None:
    for key in ("first", "second"):
        cache.certify_incremental(key)
        cache.remember_partitions(key, {})
    assert len(cache) == 0
    for key in ("first", "second"):
        cache.put(key, cached_plan)
    assert len(cache) == 2

    cache.capacity = 1
    cache.put("third", cached_plan)
    assert len(cache) == 1
    for key in ("first", "second"):
        assert cache.admits_incremental(key)
        assert cache.partition_hint(key) == {}
    cache.certify_incremental("third")
    assert not cache.admits_incremental("first")
    assert not cache.admits_incremental("second")
    assert cache.admits_incremental("third")
    assert cache.partition_hint("first") == {}
    assert cache.partition_hint("second") == {}
    assert cache.get("third") is cached_plan


def test_replacement_keeps_the_new_plan_without_growing_the_cache(
    cache: PlanCache[scene.ComponentsV2], cached_plan: CachedPlan[scene.ComponentsV2]
) -> None:
    replacement = CachedPlan(cached_plan.scene, PlanReport())
    cache.put("plan", cached_plan)
    cache.put("plan", replacement)
    assert cache.get("plan") is replacement
    assert len(cache) == 1


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

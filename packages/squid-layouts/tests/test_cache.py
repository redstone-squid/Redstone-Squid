"""Resolved-plan caching is bounded, fast, and rebinds current callbacks."""

from time import perf_counter

from squid_layouts import Action, Actions, List, ListItem, PlanCache, PresentationSession, compose, plan
from squid_layouts.cache import CachedPlan
from squid_layouts.discord import DISCORD_V2
from squid_layouts.scene import PlanReport, SceneDocument
from squid_layouts.scene_codec import SceneCodec


async def _first(_event) -> None: ...


async def _second(_event) -> None: ...


def test_cache_hit_reuses_structure_and_rebinds_current_handler() -> None:
    cache = PlanCache()
    session = PresentationSession()
    first = plan(Actions((Action("run", "Run", _first),), key="tools"), target=DISCORD_V2, session=session, cache=cache)
    second = plan(
        Actions((Action("run", "Run", _second),), key="tools"), target=DISCORD_V2, session=session, cache=cache
    )

    assert not first.metrics.cache_hit
    assert second.metrics.cache_hit
    assert second.scene is first.scene
    assert second.bindings["run"].handler is _second


def test_plan_cache_evicts_the_least_recently_used_entry() -> None:
    cache = PlanCache(capacity=2)
    scene = SceneDocument(SceneCodec.protocol, "discord.components-v2", 1, ())
    cached = CachedPlan(scene, PlanReport())

    cache.put("first", cached)
    cache.put("second", cached)
    assert cache.get("first") is cached
    cache.put("third", cached)

    assert cache.get("second") is None
    assert len(cache) == 2


def test_realistic_queue_plan_and_draw_meets_latency_budget() -> None:
    document = List(
        tuple(ListItem(str(index), f"Build {index}: compact queue status and author") for index in range(36)),
        key="build-queue",
    )

    cold = []
    for _ in range(15):
        started = perf_counter()
        compose(document, cache=PlanCache())
        cold.append(perf_counter() - started)

    cache = PlanCache()
    compose(document, cache=cache)
    warm = []
    for _ in range(50):
        started = perf_counter()
        result = compose(document, cache=cache)
        warm.append(perf_counter() - started)
        assert result.plan.metrics.cache_hit

    assert _p95(cold) < 0.100
    assert _p95(warm) < 0.010


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * 0.95)]

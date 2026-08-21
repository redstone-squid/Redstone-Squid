"""Resolved-plan caching is bounded, fast, and rebinds current callbacks."""

from dataclasses import replace
from time import perf_counter

from squid_layouts import (
    Action,
    Actions,
    List,
    ListItem,
    Localization,
    Message,
    Paragraph,
    plan,
)
from squid_layouts.discord import DEFAULT_TARGET, compose
from squid_layouts.planning import PlanCache
from squid_layouts.planning.cache import CachedPlan
from squid_layouts.primitives import (
    Button,
    Code,
    Paginate,
    Row,
)
from squid_layouts.runtime import PresentationSession
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import PlanReport, SceneDocument


async def _first(_event) -> None: ...


async def _second(_event) -> None: ...


async def _previous(_event) -> None: ...


async def _next(_event) -> None: ...


def test_cache_hit_reuses_structure_and_rebinds_current_handler() -> None:
    cache = PlanCache()
    session = PresentationSession()
    first = plan(
        Actions((Action("run", "Run", _first),), key="tools"), target=DEFAULT_TARGET, session=session, cache=cache
    )
    second = plan(
        Actions((Action("run", "Run", _second),), key="tools"), target=DEFAULT_TARGET, session=session, cache=cache
    )

    assert not first.metrics.cache_hit
    assert second.metrics.cache_hit
    assert second.scene is first.scene
    assert second.bindings["run"].handler is _second


def test_cache_hit_reuses_the_global_assignment_without_solving(monkeypatch) -> None:
    import squid_layouts.planning.planner as planner_module

    attempts = 0
    original = planner_module.solve

    def counted(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_module, "solve", counted)
    cache = PlanCache()
    document = (
        *(Paragraph(f"component {index}") for index in range(35)),
        Actions(
            tuple(Action(f"run.{index}", f"Run {index}", _first) for index in range(5)),
            key="tools",
        ),
    )

    miss = plan(document, target=DEFAULT_TARGET, cache=cache)
    hit = plan(document, target=DEFAULT_TARGET, cache=cache)

    assert attempts == miss.metrics.states_explored == 2
    assert hit.metrics == replace(miss.metrics, cache_hit=True)
    assert hit.scene is miss.scene


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


def test_cache_hit_rebinds_solver_generated_pager_controls() -> None:
    cache = PlanCache()

    def nav(key: str, _page: int, _pages: int):
        return (Row((Button("Previous", _previous, f"prev.{key}"), Button("Next", _next, f"next.{key}"))),)

    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    plan(document, target=DEFAULT_TARGET, nav=nav, cache=cache)
    cached = plan(document, target=DEFAULT_TARGET, nav=nav, cache=cache)

    assert cached.metrics.cache_hit
    assert cached.bindings["prev.traceback"].handler is _previous
    assert cached.bindings["next.traceback"].handler is _next


def test_a_cache_hit_stages_the_same_session_writes_as_a_miss() -> None:
    """The session is part of the key, so a hit must not silently skip its writes."""
    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    miss = plan(document, target=DEFAULT_TARGET, session=PresentationSession())

    cache = PlanCache()
    plan(document, target=DEFAULT_TARGET, session=PresentationSession(), cache=cache)
    hit = plan(document, target=DEFAULT_TARGET, session=PresentationSession(), cache=cache)

    assert hit.metrics.cache_hit
    assert hit.session_updates == miss.session_updates
    assert hit.session_updates


def test_plan_cache_separates_locales() -> None:
    cache = PlanCache()
    document = Paragraph(Message("Hello"))
    english = Localization("en", gettext=lambda message: message)
    translated = Localization("xx", gettext=lambda _message: "Bonjour")

    first = plan(document, target=DEFAULT_TARGET, localization=english, cache=cache)
    second = plan(document, target=DEFAULT_TARGET, localization=translated, cache=cache)

    assert not second.metrics.cache_hit
    assert first.scene != second.scene


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

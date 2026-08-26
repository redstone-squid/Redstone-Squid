"""Resolved-plan caching is bounded, fast, and rebinds current callbacks."""

from dataclasses import replace
from time import perf_counter

from squid_discord import DISCORD_V2_DPY27, compose
from squid_layouts import Palette, fallback, scene
from squid_layouts.planning import PlanCache, plan
from squid_layouts.planning.adapter import AdapterProfile
from squid_layouts.planning.cache import CachedPlan
from squid_layouts.planning.discord import components_v2_target
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.types import DiscordAdapter
from squid_layouts.primitives import (
    Button,
    Code,
    Paginate,
    Panel,
    Row,
    Text,
    Variant,
    Variants,
)
from squid_layouts.runtime import PresentationSession
from squid_layouts.scene.model import PlanReport
from squid_layouts.semantic import Action, Actions, Heading, List, ListItem, Paragraph, Section, Stack
from squid_layouts.text import Localization, Message


def _target(name: str, *, capabilities: frozenset[str] = frozenset(), limits: V2Limits = LIMITS):
    """A V2 target whose adapter supplies exactly `capabilities` and no extensions.

    Capabilities that are not Discord protocol facts belong to the adapter axis, which is
    what lets a test vary them without inventing a dialect.
    """
    return components_v2_target(AdapterProfile(DiscordAdapter, name, ">=1", capabilities=capabilities), limits=limits)


async def _first(_event) -> None: ...


async def _second(_event) -> None: ...


async def _previous(_event) -> None: ...


async def _next(_event) -> None: ...


def test_palette_is_part_of_plan_cache_identity() -> None:
    cache = PlanCache()
    document = Section(Heading("Brand"), (Paragraph("brand"),))

    first = plan(document, target=DISCORD_V2_DPY27, palette=Palette(brand=0x111111), cache=cache)
    second = plan(document, target=DISCORD_V2_DPY27, palette=Palette(brand=0x222222), cache=cache)

    assert not second.metrics.cache_hit
    assert isinstance(first.scene.components_v2.children[0], scene.Panel)
    assert isinstance(second.scene.components_v2.children[0], scene.Panel)
    assert (first.scene.components_v2.children[0].accent, second.scene.components_v2.children[0].accent) == (
        0x111111,
        0x222222,
    )


def test_plan_cache_separates_targets_with_different_capabilities() -> None:
    cache = PlanCache()
    document = Variants(
        (
            Variant((Text("rich"),), requires=frozenset({"rich-text"})),
            Variant((Text("plain"),)),
        )
    )
    basic = _target("test")
    rich = _target("rich", capabilities=frozenset({"rich-text"}))

    first = plan(document, target=basic, cache=cache)
    second = plan(document, target=rich, cache=cache)

    assert not second.metrics.cache_hit
    assert first.scene.components_v2.children == (scene.Text("plain"),)
    assert second.scene.components_v2.children == (scene.Text("rich"),)


def test_cache_hit_reuses_structure_and_rebinds_current_handler() -> None:
    cache = PlanCache()
    session = PresentationSession()
    first = plan(
        Actions((Action("run", "Run", _first),), key="tools"), target=DISCORD_V2_DPY27, session=session, cache=cache
    )
    second = plan(
        Actions((Action("run", "Run", _second),), key="tools"), target=DISCORD_V2_DPY27, session=session, cache=cache
    )

    assert not first.metrics.cache_hit
    assert second.metrics.cache_hit
    assert second.scene is first.scene
    assert second.bindings["run"].handler is _second


def test_cache_hit_reuses_every_decision_without_measuring(monkeypatch) -> None:
    import squid_layouts.planning.planner as planner_module

    attempts = 0
    original = planner_module.measure

    def counted(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_module, "measure", counted)
    cache = PlanCache()
    document = (
        *(Paragraph(f"component {index}") for index in range(35)),
        Actions(
            tuple(Action(f"run.{index}", f"Run {index}", _first) for index in range(5)),
            key="tools",
        ),
    )

    miss = plan(document, target=DISCORD_V2_DPY27, cache=cache)
    hit = plan(document, target=DISCORD_V2_DPY27, cache=cache)

    assert attempts == miss.metrics.states_explored == 2
    assert hit.metrics == replace(miss.metrics, cache_hit=True)
    assert hit.scene is miss.scene


def test_cache_hit_reuses_variant_positions_and_rebinds_the_selected_rung() -> None:
    cache = PlanCache()

    def document(handler):
        return (
            *(Text(f"filler {index}") for index in range(35)),
            Variants.of(
                Panel(tuple(Text(f"detail {index}") for index in range(5))),
                Row((Button("Run", handler, "run"),)),
            ),
        )

    miss = plan(document(_first), target=DISCORD_V2_DPY27, cache=cache)
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert not miss.metrics.cache_hit
    assert hit.metrics.cache_hit
    assert hit.scene is miss.scene
    assert hit.bindings["run"].handler is _second


def test_cache_hit_restores_a_fallback_branch_and_rebinds_it(monkeypatch) -> None:
    """All three decision classes travel in the entry, so a hit never re-searches."""
    import squid_layouts.planning.planner as planner_module

    cache = PlanCache()

    def document(handler):
        return (
            *(Paragraph(f"component {index}") for index in range(35)),
            fallback(
                Stack(tuple(Paragraph(f"primary {index}") for index in range(10))),
                Actions((Action("run", "Run", handler),), key="fallback-actions"),
            ),
        )

    miss = plan(document(_first), target=DISCORD_V2_DPY27, cache=cache)
    monkeypatch.setattr(planner_module, "measure", _never_measured)
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert hit.metrics.cache_hit
    assert hit.scene is miss.scene
    assert hit.bindings["run"].handler is _second


def _never_measured(*_args, **_kwargs):
    message = "a cache hit must not measure"
    raise AssertionError(message)


def test_plan_cache_evicts_the_least_recently_used_entry() -> None:
    cache = PlanCache(capacity=2)
    document = scene.Document(scene.Codec.protocol, "discord.components-v2", 1, scene.ComponentsV2(()))
    cached = CachedPlan(document, PlanReport())

    cache.put("first", cached)
    cache.put("second", cached)
    assert cache.get("first") is cached
    cache.put("third", cached)

    assert cache.get("second") is None
    assert len(cache) == 2


def test_cache_hit_rebinds_solver_generated_pager_controls() -> None:
    cache = PlanCache()

    def nav(state):
        return (
            Row(
                (
                    Button("Previous", _previous, f"prev.{state.key}"),
                    Button("Next", _next, f"next.{state.key}"),
                )
            ),
        )

    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    plan(document, target=DISCORD_V2_DPY27, nav=nav, cache=cache)
    cached = plan(document, target=DISCORD_V2_DPY27, nav=nav, cache=cache)

    assert cached.metrics.cache_hit
    assert cached.bindings["prev.traceback"].handler is _previous
    assert cached.bindings["next.traceback"].handler is _next


def test_a_cache_hit_stages_the_same_session_writes_as_a_miss() -> None:
    """The session is part of the key, so a hit must not silently skip its writes."""
    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    miss = plan(document, target=DISCORD_V2_DPY27, session=PresentationSession())

    cache = PlanCache()
    plan(document, target=DISCORD_V2_DPY27, session=PresentationSession(), cache=cache)
    hit = plan(document, target=DISCORD_V2_DPY27, session=PresentationSession(), cache=cache)

    assert hit.metrics.cache_hit
    assert hit.session_updates == miss.session_updates
    assert hit.session_updates


def test_plan_cache_separates_locales() -> None:
    cache = PlanCache()
    document = Paragraph(Message("Hello"))
    english = Localization("en", gettext=lambda message: message)
    translated = Localization("xx", gettext=lambda _message: "Bonjour")

    first = plan(document, target=DISCORD_V2_DPY27, localization=english, cache=cache)
    second = plan(document, target=DISCORD_V2_DPY27, localization=translated, cache=cache)

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

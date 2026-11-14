"""Reuse planned layouts while binding controls to the current callbacks and presentation state.

Tests distinguish structural cache hits, exact memo reuse, and incremental replanning when text
grows within the available space. They also cover eviction and representative latency limits.
"""

from dataclasses import replace
from time import perf_counter

import pytest

from squid_i18n import Text as DeferredText
from squid_i18n import md, tr
from squid_ui import Palette, Role, Tone, fallback, form, scene
from squid_ui.forms import FormSpec, TextField
from squid_ui.ownership import Controlled
from squid_ui.planning import PlanCache, PlanMemo, plan
from squid_ui.planning.adapter import AdapterCapability
from squid_ui.planning.cache import CachedPlan
from squid_ui.planning.navigation import TurnPage, default_nav
from squid_ui.planning.result import PlanMetrics, PlanReport, PlanReuse
from squid_ui.planning.semantic_adaptation.handlers import ChooseChoice
from squid_ui.primitives import (
    Button,
    Code,
    Paginate,
    Panel,
    Row,
    Text,
    Variant,
    Variants,
)
from squid_ui.primitives.constraints import PageEvent
from squid_ui.runtime import PresentationState
from squid_ui.semantic import (
    ActionControl,
    ActionControls,
    Choice,
    Choices,
    Heading,
    List,
    ListItem,
    Paragraph,
    Section,
    Stack,
    Status,
    Uncontrolled,
)
from squid_ui.text import Localization, Markup
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, render_message
from squid_ui_discord import testing as sd


async def _first(_event) -> None: ...


async def _second(_event) -> None: ...


async def _previous(_event) -> None: ...


async def _next(_event) -> None: ...


async def _submitted_first(_event) -> None: ...


async def _submitted_second(_event) -> None: ...


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
            Variant((Text("rich"),), requires=frozenset({AdapterCapability.RENDER_HTML})),
            Variant((Text("plain"),)),
        )
    )
    basic = sd.target_profile("test")
    rich = sd.target_profile("rich", capabilities=frozenset({AdapterCapability.RENDER_HTML}))

    first = plan(document, target=basic, cache=cache)
    second = plan(document, target=rich, cache=cache)

    assert not second.metrics.cache_hit
    assert first.scene.components_v2.children == (scene.Text("plain", markup=Markup.DISCORD_MARKDOWN),)
    assert second.scene.components_v2.children == (scene.Text("rich", markup=Markup.DISCORD_MARKDOWN),)


def test_cache_hit_reuses_structure_and_rebinds_current_handler() -> None:
    cache = PlanCache()
    session = PresentationState()
    first = plan(
        ActionControls((ActionControl("run", "Run", _first),), key="tools"),
        target=DISCORD_V2_DPY27,
        session=session,
        cache=cache,
    )
    second = plan(
        ActionControls((ActionControl("run", "Run", _second),), key="tools"),
        target=DISCORD_V2_DPY27,
        session=session,
        cache=cache,
    )

    assert not first.metrics.cache_hit
    assert second.metrics.cache_hit
    assert second.scene is first.scene
    assert second.bindings["run"].handler is _second


def test_cache_hit_reuses_every_decision_without_measuring(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    attempts = 0
    original = planner_module.measure  # pyright: ignore[reportPrivateImportUsage]  # monkeypatch target: the caller resolves it here

    def counted(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_module, "measure", counted)
    cache = PlanCache()

    def document(handler):
        return (
            *(Paragraph(f"component {index}") for index in range(35)),
            ActionControls(
                tuple(ActionControl(f"run.{index}", f"Run {index}", handler) for index in range(5)),
                key="tools",
            ),
        )

    miss = plan(document(_first), target=DISCORD_V2_DPY27, cache=cache)
    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert attempts == miss.metrics.states_explored == 2
    assert hit.metrics == replace(miss.metrics, cache_hit=True, reuse=PlanReuse.STRUCTURAL)
    assert hit.scene is miss.scene
    assert all(route.handler is _second for route in hit.bindings["tools.default.0"].routes.values())


def test_structural_program_rebinds_generated_form_adapter_without_lowering(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()
    spec = FormSpec("Edit", (TextField(key="name", label="Name"),))
    first = plan(
        form("Edit", spec, key="edit", on_submit=_submitted_first),
        target=DISCORD_V2_DPY27,
        cache=cache,
    )

    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    hit = plan(
        form("Edit", spec, key="edit", on_submit=_submitted_second),
        target=DISCORD_V2_DPY27,
        cache=cache,
    )

    assert hit.scene is first.scene
    assert hit.form_bindings["edit"].on_submit is _submitted_second


def test_structural_program_rebinds_managed_controls_to_the_current_session(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()
    document = Choices("pick", (Choice("a", "A"), Choice("b", "B")), Uncontrolled(()))
    first_session = PresentationState()
    current_session = PresentationState()
    plan(document, target=DISCORD_V2_DPY27, session=first_session, cache=cache)

    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    hit = plan(document, target=DISCORD_V2_DPY27, session=current_session, cache=cache)

    handler = hit.bindings["pick.a"].handler
    assert isinstance(handler, ChooseChoice)
    assert handler.commit.session is current_session


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
    """A cached fallback choice must retain its layout while using the new callback."""
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()

    def document(handler):
        return (
            *(Paragraph(f"component {index}") for index in range(35)),
            fallback(
                Stack(tuple(Paragraph(f"primary {index}") for index in range(10))),
                ActionControls((ActionControl("run", "Run", handler),), key="fallback-actions"),
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
    document = scene.Scene(scene.Codec.protocol, "discord.components-v2", 1, scene.ComponentsV2(()))
    cached = CachedPlan(document, PlanReport())

    cache.put("first", cached)
    cache.put("second", cached)
    assert cache.get("first") is cached
    cache.put("third", cached)

    assert cache.get("second") is None
    assert len(cache) == 2


def test_cache_hit_sends_controlled_page_turns_to_the_current_render(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()

    async def first(event: PageEvent) -> None: ...

    async def second(event: PageEvent) -> None: ...

    def document(on_change):
        return Code("x" * 9000, overflow=Paginate(key="traceback", page=Controlled(1, on_change)))

    plan(document(first), target=DISCORD_V2_DPY27, nav=default_nav, cache=cache)
    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    cached = plan(document(second), target=DISCORD_V2_DPY27, nav=default_nav, cache=cache)

    assert cached.metrics.cache_hit
    handler = cached.bindings["__cursor_next.traceback"].handler
    assert isinstance(handler, TurnPage)
    assert handler.commit.owner == Controlled(1, second)


def test_cache_hit_rebinds_solver_generated_pager_controls(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()

    def nav(context):
        return (
            Row(
                (
                    Button("Previous", _previous, f"prev.{context.key}"),
                    Button("Next", _next, f"next.{context.key}"),
                )
            ),
        )

    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    plan(document, target=DISCORD_V2_DPY27, nav=nav, cache=cache)
    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    cached = plan(document, target=DISCORD_V2_DPY27, nav=nav, cache=cache)

    assert cached.metrics.cache_hit
    assert cached.bindings["prev.traceback"].handler is _previous
    assert cached.bindings["next.traceback"].handler is _next


def test_a_cache_hit_stages_the_same_session_writes_as_a_miss() -> None:
    """Reusing a layout must still initialize its pager in the current presentation state."""
    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    miss = plan(document, target=DISCORD_V2_DPY27, session=PresentationState())

    cache = PlanCache()
    plan(document, target=DISCORD_V2_DPY27, session=PresentationState(), cache=cache)
    hit = plan(document, target=DISCORD_V2_DPY27, session=PresentationState(), cache=cache)

    assert hit.metrics.cache_hit
    assert hit.session_updates == miss.session_updates
    assert hit.session_updates


def test_exact_memo_skips_cache_key_lowering_and_binding_collection(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    document = ActionControls((ActionControl("run", "Run", _first),), key="tools")
    session = PresentationState()
    memo = PlanMemo()
    first = plan(document, target=DISCORD_V2_DPY27, session=session, cache=PlanCache(), memo=memo)

    monkeypatch.setattr(planner_module, "_plan_cache_key", _never_measured)
    monkeypatch.setattr(planner_module, "lower_semantics", _never_measured)
    exact = plan(document, target=DISCORD_V2_DPY27, session=session, cache=PlanCache(), memo=memo)

    assert exact.scene is first.scene
    assert exact.bindings is first.bindings
    assert exact.metrics.reuse is PlanReuse.EXACT


def test_lossless_text_growth_replans_locally_without_global_search(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cases = []
    for make_document in (Text, Paragraph):
        expected = plan(make_document("x" * 2500), target=DISCORD_V2_DPY27)
        cache = PlanCache()
        plan(make_document("x" * 2000), target=DISCORD_V2_DPY27, cache=cache)
        cases.append((make_document, expected, cache))

    monkeypatch.setattr(planner_module, "_search", _never_measured)
    for make_document, expected, cache in cases:
        incremental = plan(make_document("x" * 2500), target=DISCORD_V2_DPY27, cache=cache)

        assert incremental.scene == expected.scene
        assert incremental.report == expected.report
        assert incremental.metrics == PlanMetrics(
            states_explored=1,
            cache_hit=True,
            reuse=PlanReuse.INCREMENTAL,
        )


def test_classic_lossless_text_growth_replans_locally_without_global_search(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    expected = plan(Text("x" * 2500), target=DISCORD_V1_DPY27)
    cache = PlanCache()
    plan(Text("x" * 2000), target=DISCORD_V1_DPY27, cache=cache)

    monkeypatch.setattr(planner_module, "_search", _never_measured)
    incremental = plan(Text("x" * 2500), target=DISCORD_V1_DPY27, cache=cache)

    assert incremental.scene == expected.scene
    assert incremental.report == expected.report
    assert incremental.metrics == PlanMetrics(
        states_explored=1,
        cache_hit=True,
        reuse=PlanReuse.INCREMENTAL,
    )


def test_incremental_text_growth_falls_back_when_it_crosses_headroom(monkeypatch) -> None:
    import squid_ui.planning.discord_planner as planner_module

    cache = PlanCache()
    plan(Text("x" * 2000), target=DISCORD_V2_DPY27, cache=cache)
    searches = 0
    original = planner_module._search

    def counted(*args, **kwargs):
        nonlocal searches
        searches += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(planner_module, "_search", counted)
    result = plan(Text("x" * 4500), target=DISCORD_V2_DPY27, cache=cache)

    assert searches == 1
    assert result.metrics.reuse is PlanReuse.MISS
    assert not result.metrics.cache_hit


def test_plan_cache_separates_locales() -> None:
    cache = PlanCache()
    document = Paragraph(tr(t"test-hello = Hello"))
    english = Localization("en")
    translated = Localization("fr", ("test-hello = Bonjour",))

    first = plan(document, target=DISCORD_V2_DPY27, localization=english, cache=cache)
    second = plan(document, target=DISCORD_V2_DPY27, localization=translated, cache=cache)

    assert not second.metrics.cache_hit
    assert first.scene != second.scene


@pytest.mark.parametrize(
    ("first_text", "second_text"),
    [
        (DeferredText("first"), DeferredText("second")),
        (DeferredText("**literal**"), md("**literal**")),
        (tr("Hello", key="hello"), tr("Hello", key="other")),
        (md(t"**{DeferredText('first')}**"), md(t"**{DeferredText('second')}**")),
    ],
)
def test_plan_cache_distinguishes_unresolved_text(first_text: DeferredText, second_text: DeferredText) -> None:
    cache = PlanCache()
    plan(Paragraph(first_text), target=DISCORD_V2_DPY27, cache=cache)
    changed = plan(Paragraph(second_text), target=DISCORD_V2_DPY27, cache=cache)
    fresh = plan(Paragraph(second_text), target=DISCORD_V2_DPY27)

    assert changed.metrics.reuse is PlanReuse.INCREMENTAL
    assert changed.scene == fresh.scene


def test_plan_cache_reuses_equal_translations_and_invalidates_changed_bindings() -> None:
    def document(name: str) -> Paragraph:
        greeting = tr(t"Hello {name}", key="greeting")
        return Paragraph(DeferredText(t"Notice: {greeting}"))

    cache = PlanCache()
    localization = Localization("fr", ("greeting = Bonjour { $name }",))
    first = plan(document("Ann"), target=DISCORD_V2_DPY27, localization=localization, cache=cache)
    same = plan(document("Ann"), target=DISCORD_V2_DPY27, localization=localization, cache=cache)
    changed = plan(document("Bob"), target=DISCORD_V2_DPY27, localization=localization, cache=cache)
    fresh = plan(document("Bob"), target=DISCORD_V2_DPY27, localization=localization)

    assert same.metrics.cache_hit
    assert same.scene == first.scene
    assert changed.metrics.reuse is PlanReuse.INCREMENTAL
    assert changed.scene == fresh.scene
    assert changed.scene != first.scene


def test_realistic_queue_plan_and_draw_meets_latency_budget() -> None:
    document = List(
        tuple(ListItem(str(index), f"Build {index}: compact queue status and author") for index in range(36)),
        key="build-queue",
    )

    cold = []
    for _ in range(15):
        started = perf_counter()
        render_message(document, cache=PlanCache())
        cold.append(perf_counter() - started)

    cache = PlanCache()
    render_message(document, cache=cache)
    warm = []
    for _ in range(50):
        started = perf_counter()
        result = render_message(document, cache=cache)
        warm.append(perf_counter() - started)
        assert result.plan.metrics.cache_hit

    assert _p95(cold) < 0.100
    assert _p95(warm) < 0.010


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[int((len(ordered) - 1) * 0.95)]


def test_custom_role_overrides_and_markers_invalidate_cached_plans() -> None:
    cache = PlanCache()
    role = Role("build.pending", Tone.WARNING, marker="⏳ ")
    document = Section(Heading("Build"), (Paragraph("pending"),), accent=role)
    first = plan(document, target=DISCORD_V2_DPY27, palette=Palette(roles={role.name: 0x111111}), cache=cache)
    second = plan(document, target=DISCORD_V2_DPY27, palette=Palette(roles={role.name: 0x222222}), cache=cache)
    assert not second.metrics.cache_hit
    assert first.scene != second.scene

    before = plan(Status("Pending", tone=role), target=DISCORD_V2_DPY27, cache=cache)
    after = plan(Status("Pending", tone=replace(role, marker="")), target=DISCORD_V2_DPY27, cache=cache)
    assert not after.metrics.cache_hit
    assert before.scene != after.scene

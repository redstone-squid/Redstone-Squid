"""Reuse planned layouts while binding controls to the current callbacks and presentation state.

Tests distinguish structural cache hits, exact snapshot reuse, and incremental replanning when text
grows within the available space. They also cover eviction and representative latency limits.
"""

from dataclasses import replace
from time import perf_counter

import pytest

from squid_i18n import Text as DeferredText
from squid_i18n import md, tr
from squid_ui import Palette, Role, Tone, fallback, form
from squid_ui.authoring.presentations import corresponds, losses, omit, ref
from squid_ui.authoring.semantic import (
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
from squid_ui.content.text import Localization, Markup
from squid_ui.ext.discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, render_message
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.navigation_controls import default_nav
from squid_ui.ext.discord.primitives import Button, Code, Paginate, Panel, Row, Text, Variant, Variants
from squid_ui.ext.html.target_types import HtmlAdapterCapability
from squid_ui.interactions.forms import FormSpec, TextField
from squid_ui.interactions.ownership import Controlled
from squid_ui.overflow import PageEvent
from squid_ui.planning import PlanCache, PlanningSnapshot, plan
from squid_ui.planning.interaction_handlers import SelectChoices
from squid_ui.planning.pagination.navigation import TurnPage
from squid_ui.planning.result import PlanReuse
from squid_ui.runtime import PresentationState


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
    first_panel = first.scene.expect_body(discord_scene.ComponentsV2).children[0]
    second_panel = second.scene.expect_body(discord_scene.ComponentsV2).children[0]
    assert isinstance(first_panel, discord_scene.Panel)
    assert isinstance(second_panel, discord_scene.Panel)
    assert (
        first_panel.accent,
        second_panel.accent,
    ) == (
        0x111111,
        0x222222,
    )


def test_plan_cache_separates_targets_with_different_capabilities() -> None:
    cache = PlanCache()
    document = Variants(
        (
            Variant((Text("rich"),), requires=frozenset({HtmlAdapterCapability.RENDER_HTML})),
            Variant((Text("plain"),)),
        )
    )
    basic = sd.target_profile("test")
    rich = sd.target_profile("rich", capabilities=frozenset({HtmlAdapterCapability.RENDER_HTML}))

    first = plan(document, target=basic, cache=cache)
    second = plan(document, target=rich, cache=cache)

    assert not second.metrics.cache_hit
    assert first.scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("plain", markup=Markup.DISCORD_MARKDOWN),
    )
    assert second.scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("rich", markup=Markup.DISCORD_MARKDOWN),
    )


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
    assert second.metrics.partition_hint
    assert second.scene == first.scene
    assert second.bindings["run"].handler is _second


def test_partition_hint_recertifies_the_selected_controls(monkeypatch) -> None:
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
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert hit.metrics.partition_hint
    assert hit.metrics.target_evaluations > 0
    assert hit.scene == miss.scene
    menu = next(
        node
        for node in hit.scene.expect_body(discord_scene.ComponentsV2).children
        if isinstance(node, discord_scene.Select)
    )
    assert all(route.handler is _second for route in hit.bindings[menu.action].routes.values())


def test_partition_hint_rebinds_the_current_form_handler(monkeypatch) -> None:
    cache = PlanCache()
    spec = FormSpec("Edit", (TextField(key="name", label="Name"),))
    first = plan(
        form("Edit", spec, key="edit", on_submit=_submitted_first),
        target=DISCORD_V2_DPY27,
        cache=cache,
    )

    hit = plan(
        form("Edit", spec, key="edit", on_submit=_submitted_second),
        target=DISCORD_V2_DPY27,
        cache=cache,
    )

    assert hit.scene == first.scene
    assert hit.form_bindings["edit"].on_submit is _submitted_second


def test_structural_program_rebinds_managed_controls_to_the_current_session(monkeypatch) -> None:
    cache = PlanCache()
    document = Choices("pick", (Choice("a", "A"), Choice("b", "B")), Uncontrolled(()))
    first_session = PresentationState()
    current_session = PresentationState()
    plan(document, target=DISCORD_V2_DPY27, session=first_session, cache=cache)

    hit = plan(document, target=DISCORD_V2_DPY27, session=current_session, cache=cache)

    handler = hit.bindings["pick"].handler
    assert isinstance(handler, SelectChoices)
    assert handler.commit.session is current_session


def test_cache_hit_reuses_variant_positions_and_rebinds_the_selected_rung() -> None:
    cache = PlanCache()

    def document(handler):
        button = Button("Run", handler, "run")
        return (
            *(Text(f"filler {index}") for index in range(35)),
            Variants(
                (
                    Variant(
                        (Panel((*tuple(Text(f"detail {index}") for index in range(5)), Row((button,)))),),
                        correspondence=tuple(
                            corresponds(ref("@content", "@0", f"@{index}"), "details") for index in range(5)
                        ),
                    ),
                    Variant((Row((button,)),), contract=losses(omit("details"))),
                ),
                content=Stack((Paragraph("Details", key="details"), button)),
            ),
        )

    miss = plan(document(_first), target=DISCORD_V2_DPY27, cache=cache)
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert not miss.metrics.partition_hint
    assert hit.metrics.partition_hint
    assert hit.metrics.target_evaluations > 0
    assert hit.scene == miss.scene
    assert hit.bindings["run"].handler is _second


def test_cached_fallback_hint_recertifies_the_current_callback() -> None:
    """Shared hints reuse layout choices without retaining a previous render's callbacks."""
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
    hit = plan(document(_second), target=DISCORD_V2_DPY27, cache=cache)

    assert hit.metrics.partition_hint
    assert hit.metrics.target_evaluations > 0
    assert hit.scene == miss.scene
    assert hit.bindings["run"].handler is _second


def _never_measured(*_args, **_kwargs):
    message = "a cache hit must not measure"
    raise AssertionError(message)


def test_plan_cache_evicts_the_least_recently_used_entry() -> None:
    cache = PlanCache(capacity=2)
    cache.remember_partitions("first", {})
    cache.remember_partitions("second", {})
    assert cache.partition_hint("first") == {}
    cache.remember_partitions("third", {})
    assert cache.partition_hint("second") is None
    assert len(cache) == 2


def test_shared_pagination_sends_controlled_page_turns_to_the_current_render(monkeypatch) -> None:
    cache = PlanCache()

    async def first(event: PageEvent) -> None: ...

    async def second(event: PageEvent) -> None: ...

    def document(on_change):
        return Code("x" * 9000, overflow=Paginate(key="traceback", page=Controlled(1, on_change)))

    plan(document(first), target=DISCORD_V2_DPY27, nav=default_nav, cache=cache)
    cached = plan(document(second), target=DISCORD_V2_DPY27, nav=default_nav, cache=cache)

    assert cached.allocation is not None
    handler = cached.bindings["__cursor_next.traceback"].handler
    assert isinstance(handler, TurnPage)
    assert handler.commit.owner == Controlled(1, second)


def test_shared_pagination_binds_custom_pager_controls(monkeypatch) -> None:
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
    cached = plan(document, target=DISCORD_V2_DPY27, nav=nav, cache=cache)

    assert cached.allocation is not None
    assert cached.bindings["prev.traceback"].handler is _previous
    assert cached.bindings["next.traceback"].handler is _next


def test_shared_pagination_stages_equivalent_cursors_with_or_without_a_cache() -> None:
    document = Code("x" * 9000, overflow=Paginate(key="traceback"))
    miss = plan(document, target=DISCORD_V2_DPY27, session=PresentationState())

    cache = PlanCache()
    plan(document, target=DISCORD_V2_DPY27, session=PresentationState(), cache=cache)
    hit = plan(document, target=DISCORD_V2_DPY27, session=PresentationState(), cache=cache)

    assert hit.allocation is not None
    hit_state, miss_state = PresentationState(), PresentationState()
    hit_state.apply_updates(hit.session_updates)
    miss_state.apply_updates(miss.session_updates)
    assert hit_state.cursors == miss_state.cursors
    assert hit.scene == miss.scene


def test_exact_memo_skips_cache_key_lowering_and_binding_collection(monkeypatch) -> None:
    document = ActionControls((ActionControl("run", "Run", _first),), key="tools")
    session = PresentationState()
    snapshot = PlanningSnapshot()
    first = plan(document, target=DISCORD_V2_DPY27, session=session, cache=PlanCache(), snapshot=snapshot)

    monkeypatch.setattr("squid_ui.planning.composition_planner.RuleComposition.plan", _never_measured)
    monkeypatch.setattr("squid_ui.planning.planner.prepare_bindings", _never_measured)
    exact = plan(document, target=DISCORD_V2_DPY27, session=session, cache=PlanCache(), snapshot=snapshot)

    assert exact.scene is first.scene
    assert exact.bindings is first.bindings
    assert exact.metrics.reuse is PlanReuse.EXACT


def test_text_growth_is_recertified_against_the_current_capacity(monkeypatch) -> None:
    cases = []
    for make_document in (Text, Paragraph):
        expected = plan(make_document("x" * 2500), target=DISCORD_V2_DPY27)
        cache = PlanCache()
        plan(make_document("x" * 2000), target=DISCORD_V2_DPY27, cache=cache)
        cases.append((make_document, expected, cache))

    for make_document, expected, cache in cases:
        incremental = plan(make_document("x" * 2500), target=DISCORD_V2_DPY27, cache=cache)

        assert incremental.scene == expected.scene
        assert incremental.report.events == expected.report.events
        assert incremental.metrics.target_evaluations > 0


def test_classic_text_growth_is_recertified_against_the_current_capacity(monkeypatch) -> None:
    expected = plan(Text("x" * 2500), target=DISCORD_V1_DPY27)
    cache = PlanCache()
    plan(Text("x" * 2000), target=DISCORD_V1_DPY27, cache=cache)

    incremental = plan(Text("x" * 2500), target=DISCORD_V1_DPY27, cache=cache)

    assert incremental.scene == expected.scene
    assert incremental.report.events == expected.report.events
    assert incremental.metrics.target_evaluations > 0


def test_text_growth_searches_when_it_crosses_headroom(monkeypatch) -> None:
    cache = PlanCache()
    plan(Text("x" * 2000), target=DISCORD_V2_DPY27, cache=cache)
    result = plan(Text("x" * 4500), target=DISCORD_V2_DPY27, cache=cache)

    assert result.metrics.target_evaluations > 0
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

    assert changed.metrics.target_evaluations > 0
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

    assert same.metrics.partition_hint
    assert same.scene == first.scene
    assert changed.metrics.target_evaluations > 0
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
    session = PresentationState()
    first = render_message(document, cache=cache, session=session)
    session.apply_updates(first.plan.session_updates)
    warm = []
    for _ in range(50):
        started = perf_counter()
        result = render_message(document, cache=cache, session=session)
        warm.append(perf_counter() - started)
        assert result.plan.allocation is first.plan.allocation

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

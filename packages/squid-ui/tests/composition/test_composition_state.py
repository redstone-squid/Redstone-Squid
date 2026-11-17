"""Certified navigation observes ownership and commits only delivered positions."""

from dataclasses import replace

import pytest

import squid_ui as sl
import squid_ui.ext.html
import squid_ui.ext.slack
from squid_ui import scene
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import components_v2_target
from squid_ui.interactions.ownership import Controlled
from squid_ui.pages import PagePosition
from squid_ui.planning import CompositionOptions, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.pagination.navigation import TurnPage, default_nav, page_select_nav
from squid_ui.planning.resources import Axis, TargetLimits
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

V2 = components_v2_target(AdapterProfile(DiscordAdapter, "test", ">=1"))
REGION = OccurrenceId(("root", "r"))
OPTIONS = CompositionOptions(automatic=True, partitions={REGION: (2, 4)})


def content() -> sl.semantic.Region:
    return sl.region(*(sl.paragraph(key, key=key) for key in "abcd"), key="r", title="R")


def test_navigation_is_certified_and_cursor_only_selection_reuses_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    session = PresentationState()
    document = content()
    result = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert len(result.bindings) == 3
    assert any(isinstance(node, scene.Select) for node in result.scene.components_v2.children)
    session.apply_updates(result.session_updates)
    allocation = result.allocation
    committed = session.cursor("$/r")
    session.request_cursor("$/r", PagePosition(page=1))

    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("a cursor turn reran allocation search")

    monkeypatch.setattr("squid_ui.planning.composition_planner.search_composition", fail)
    monkeypatch.setattr("squid_ui.planning.composition_state.semantic_graph", fail)
    next_page = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert next_page.allocation is allocation
    assert next_page.scene.pagers[0].page == 1
    assert session.cursor("$/r") == committed
    retry = plan(document, target=V2, session=session, nav=page_select_nav, composition=OPTIONS)
    assert retry.scene == next_page.scene
    session.apply_updates(retry.session_updates)
    assert session.cursor("$/r").position == PagePosition("c", 1)
    assert not session.pending_positions


def test_deleted_anchor_recovers_after_repartition_and_stages_history() -> None:
    session = PresentationState()
    first = plan(
        content(),
        target=V2,
        session=session,
        nav=default_nav,
        positions={"$/r": PagePosition(page=1)},
        composition=OPTIONS,
    )
    session.apply_updates(first.session_updates)
    updated = sl.region(*(sl.paragraph(key, key=key) for key in "adb"), key="r", title="R")
    second = plan(
        updated,
        target=V2,
        session=session,
        nav=default_nav,
        composition=CompositionOptions(automatic=True, partitions={REGION: (2, 3)}),
    )
    assert second.report.regions[0].anchor == "d"
    assert second.scene.pagers[0].page == 0
    assert session.cursor("$/r").position.anchor == "c"
    session.apply_updates(second.session_updates)
    assert session.cursor("$/r").position == PagePosition("d", 0)
    assert session.cursor("$/r").source_order == ("a", "d", "b")


async def test_controlled_negative_page_dispatches_without_cursor_writes() -> None:
    selected: list[int] = []

    async def change(event: sl.PageEvent) -> None:
        selected.append(event.page)

    session = PresentationState()
    node = replace(content(), page=sl.controlled(-1, change))
    result = plan(node, target=V2, session=session, nav=default_nav, composition=OPTIONS)
    assert result.scene.pagers[0].page == 1
    session.apply_updates(result.session_updates)
    assert not session.cursors
    assert selected == []


def test_custom_navigation_cost_changes_feasibility() -> None:
    target = replace(V2, limits=V2Limits(total_components=6))
    plan(content(), target=target, nav=default_nav, composition=OPTIONS)
    with pytest.raises(sl.planning.JointInfeasibleError):
        plan(content(), target=target, nav=page_select_nav, composition=OPTIONS)


def test_controlled_cursor_movement_retains_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    async def change(event: sl.PageEvent) -> None:
        pytest.fail("planning must not call the owner")

    session = PresentationState()
    node = replace(content(), page=sl.controlled(0, change))
    first = plan(node, target=V2, session=session, nav=default_nav, composition=OPTIONS)
    session.apply_updates(first.session_updates)

    def fail(*args: object, **kwargs: object) -> None:
        pytest.fail("controlled page movement reran search")

    monkeypatch.setattr("squid_ui.planning.composition_planner.search_composition", fail)
    second = plan(
        replace(node, page=sl.controlled(1, change)), target=V2, session=session, nav=default_nav, composition=OPTIONS
    )
    assert second.allocation is first.allocation
    assert second.scene.pagers[0].page == 1
    assert not session.cursors


def test_hidden_child_recovers_from_its_last_displayed_history() -> None:
    inner = OccurrenceId(("root", "outer", "r"))
    outer = OccurrenceId(("root", "outer"))
    options = CompositionOptions(automatic=True, partitions={outer: (1, 2), inner: (2, 4)})
    node = sl.region(content(), sl.paragraph("Other page", key="other"), key="outer", title="Outer")
    session = PresentationState()
    first = plan(
        node,
        target=V2,
        session=session,
        nav=default_nav,
        positions={"$/outer/r": PagePosition(page=1)},
        composition=options,
    )
    session.apply_updates(first.session_updates)
    hidden = first.for_page({outer: 1})
    session.apply_updates(hidden.session_updates)
    assert session.cursor("$/outer/r").position.anchor == "c"
    assert session.cursor("$/outer").position.anchor == "other"
    changed = replace(
        node,
        children=(replace(content(), children=tuple(sl.paragraph(key, key=key) for key in "adb")), node.children[1]),
    )
    new_options = replace(options, partitions={outer: (1, 2), inner: (2, 3)})
    concealed = plan(changed, target=V2, session=session, nav=default_nav, composition=new_options)
    session.apply_updates(concealed.session_updates)
    assert session.cursor("$/outer/r").position.anchor == "c"
    assert session.cursor("$/outer/r").source_order == tuple("abcd")
    assert all("$/outer/r" not in key for key in concealed.bindings)
    revealed = concealed.for_page({outer: 0})
    assert next(report for report in revealed.report.regions if report.key == "$/outer/r").anchor == "d"
    session.apply_updates(revealed.session_updates)
    assert session.cursor("$/outer/r").source_order == tuple("adb")
    assert session.cursor("$/outer").position.anchor == "r"


def test_root_and_local_pages_use_shared_allocation() -> None:
    target = replace(V2, limits=V2Limits(total_components=6))
    root = plan(
        sl.Document(tuple(sl.paragraph(str(i), key=str(i)) for i in range(8)), key="document"),
        target=target,
        nav=default_nav,
    )
    assert root.allocation is not None
    assert root.scene.pagers[0].key == "document"
    local = plan(
        sl.primitives.Lines(tuple("abcd"), overflow=sl.primitives.Paginate(key="lines", per=2)),
        target=target,
        nav=default_nav,
    )
    assert local.allocation is not None
    assert local.scene.pagers[0].pages == 2


def test_explicit_search_allowance_is_enforced() -> None:
    with pytest.raises(sl.planning.JointExhaustedError):
        plan(content(), target=V2, nav=default_nav, search_budget=1)


def test_document_assets_share_attachment_capacity_with_downloads() -> None:
    asset = sl.file(b"payload", filename="shared.txt")
    node = sl.region(sl.download(asset, key="download"), key="r", title="Files")
    result = plan(sl.Document((node,), assets=(asset,)), target=V2, nav=default_nav)
    assert len(result.scene.assets) == 1
    assert result.cost.get(Axis.ATTACHMENTS) == 1


def test_changed_callback_rebuilds_bindings_in_a_retained_owner() -> None:
    async def first(event: sl.PageEvent) -> None:
        pass

    async def second(event: sl.PageEvent) -> None:
        pass

    session = PresentationState()
    original = replace(content(), page=sl.controlled(0, first))
    old = plan(original, target=V2, session=session, nav=default_nav, composition=OPTIONS)
    session.apply_updates(old.session_updates)
    changed = plan(
        replace(original, page=sl.controlled(0, second)),
        target=V2,
        session=session,
        nav=default_nav,
        composition=OPTIONS,
    )
    assert changed.allocation is not old.allocation
    binding = changed.bindings["__cursor_next.$/r"]
    assert isinstance(binding.handler, TurnPage)
    assert isinstance(binding.handler.commit.owner, Controlled)
    assert binding.handler.commit.owner.on_change is second


@pytest.mark.parametrize(
    "target",
    [
        V2,
        squid_ui.ext.html.target(),
        squid_ui.ext.slack.message_target(adapter=AdapterProfile(SlackAdapter, "state.slack", ">=1")),
    ],
    ids=["v2", "html", "slack"],
)
def test_production_list_recovers_a_deleted_anchor_across_targets[
    LimitsT: TargetLimits,
    BodyT: scene.Body,
    RenderTargetT: sl.RenderTarget,
    AdapterT,
](target: Target[LimitsT, BodyT, RenderTargetT, AdapterT]) -> None:
    session = PresentationState()
    original = sl.semantic.List(tuple(sl.semantic.ListItem(key, key) for key in "abcd"), key="items", page_size=2)
    first = plan(original, target=target, session=session, nav=default_nav, positions={"items": PagePosition(page=1)})
    assert first.allocation is not None
    session.apply_updates(first.session_updates)
    assert session.cursor("items").position.anchor == "c"
    changed = replace(original, items=tuple(item for item in original.items if item.key != "c"))
    recovered = plan(changed, target=target, session=session, nav=default_nav)
    assert recovered.report.regions[0].anchor == "d"
    assert session.cursor("items").position.anchor == "c"
    session.apply_updates(recovered.session_updates)
    assert session.cursor("items").source_order == tuple("abd")


def test_recovery_follows_source_order_when_a_presentation_omits_the_anchor() -> None:
    source = sl.stack(*(sl.paragraph(key, key=key) for key in "abcd"), key="source")
    alternatives = sl.alternatives(
        sl.alternative(source, key="full", fidelity=sl.equivalent()),
        sl.alternative(
            sl.stack(*(sl.paragraph(key, key=key) for key in "adb"), key="compact"),
            key="compact",
            fidelity=sl.losses(sl.omit("c")),
        ),
        content=source,
        key="choice",
    )
    document = sl.region(alternatives, key="r", title="R")
    session = PresentationState()
    choice = OccurrenceId(("root", "r", "choice"))
    full = replace(OPTIONS, selections={choice: "full"})
    first = plan(
        document, target=V2, session=session, nav=default_nav, composition=full, positions={"$/r": PagePosition(page=1)}
    )
    session.apply_updates(first.session_updates)
    assert session.cursor("$/r").position.anchor == "choice/source/c"
    compact = replace(OPTIONS, selections={choice: "compact"}, partitions={REGION: (2, 3)})
    result = plan(document, target=V2, session=session, nav=default_nav, composition=compact)
    assert result.report.regions[0].anchor == "choice/source/d"
    assert result.report.regions[0].page == 0


def test_controlled_region_inside_alternatives_reuses_allocation_with_current_owner() -> None:
    async def changed(event: sl.PageEvent) -> None:
        pytest.fail("planning invoked the owner")

    def document(page: int) -> sl.Renderable:
        source = replace(content(), page=sl.controlled(page, changed))
        return sl.alternatives(
            sl.alternative(source, key="full", fidelity=sl.equivalent()), content=source, key="choice"
        )

    session = PresentationState()
    identity = OccurrenceId(("root", "choice", "@presentation"))
    options = CompositionOptions(automatic=True, partitions={identity: (2, 4)})
    first = plan(document(0), target=V2, session=session, nav=default_nav, composition=options)
    session.apply_updates(first.session_updates)
    second = plan(document(1), target=V2, session=session, nav=default_nav, composition=options)
    assert second.allocation is first.allocation
    assert second.report.regions[0].page == 1
    assert second.report.regions[0].key == "$/choice/r"
    assert not session.cursors


def test_alternate_list_cannot_change_its_page_owner() -> None:
    async def changed(event: sl.PageEvent) -> None:
        pytest.fail("planning invoked the owner")

    source = sl.semantic.List(
        tuple(sl.semantic.ListItem(key, key) for key in "ab"), key="items", page_size=1, page=sl.controlled(1, changed)
    )
    node = sl.alternatives(
        sl.alternative(replace(source, page=sl.uncontrolled(0)), key="wrong-owner", fidelity=sl.equivalent()),
        content=source,
        key="choice",
    )
    with pytest.raises(sl.errors.LayoutInvariantError, match="preserve the List boundary"):
        plan(node, target=V2, nav=default_nav)

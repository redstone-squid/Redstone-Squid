"""Joint region allocation bounds every reachable page, including nested pagers."""

from itertools import product

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui import scene
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.pages import PagePosition
from squid_ui.planning import plan
from squid_ui.planning.navigation import default_nav
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.primitives.constraints import PageEvent, RoutedPage
from squid_ui_discord import DISCORD_V2_DPY27
from squid_ui_discord.testing import assert_within_limits, commit_render, http_error, interaction_harness


def target(components: int) -> Target:
    return DISCORD_V2_DPY27.reserve(ResourceCost({Axis.COMPONENTS: 40 - components}))


def region(key: str, count: int) -> sl.semantic.Region:
    return sl.region(*(sl.paragraph(f"{key}-{index}", key=str(index)) for index in range(count)), key=key, title=key)


def test_siblings_have_independent_stable_allocations() -> None:
    document = (region("left", 6), region("right", 6))
    first = plan(document, target=target(10), nav=default_nav)
    assert [item.pages for item in first.report.regions] == [6, 6]
    for left, right in product(range(6), repeat=2):
        current = plan(
            document,
            target=target(10),
            nav=default_nav,
            positions={"$/left": PagePosition(page=left), "$/right": PagePosition(page=right)},
        )
        assert current.cost == first.cost
        assert current.cost.get(Axis.COMPONENTS) == 10
        assert [item.page for item in current.report.regions] == [left, right]
        assert [item.allocation for item in current.report.regions] == [
            item.allocation for item in first.report.regions
        ]


def test_content_keys_preserve_position_when_siblings_move() -> None:
    session = PresentationState()
    original = region("items", 7)
    first = plan(
        original, target=target(6), nav=default_nav, session=session, positions={"$/items": PagePosition(page=2)}
    )
    session.apply_updates(first.session_updates)
    anchor = session.cursor("$/items").position.anchor
    updated = sl.region(sl.paragraph("new", key="new"), *original.children, key="items", title="items")
    result = plan(updated, target=target(6), nav=default_nav, session=session)
    assert result.report.regions[0].anchor == anchor
    assert session.cursor("$/items").position.anchor == anchor


def test_nested_regions_keep_hidden_positions_and_only_publish_visible_controls() -> None:
    document = sl.region(region("one", 7), region("two", 7), key="outer", title="Outer")
    session = PresentationState()
    first = plan(
        document, target=target(10), nav=default_nav, session=session, positions={"$/outer/one": PagePosition(page=1)}
    )
    session.apply_updates(first.session_updates)
    assert len(first.report.regions) == 3
    assert first.report.regions[0].pages > 1
    second = plan(
        document, target=target(10), nav=default_nav, session=session, positions={"$/outer": PagePosition(page=1)}
    )
    session.apply_updates(second.session_updates)
    assert session.cursor("$/outer/one").position.page == 1
    assert all("$/outer/one" not in key for key in second.bindings)


def test_persistent_controls_are_reserved_on_every_page() -> None:
    document = sl.region(
        *region("body", 7).children,
        key="items",
        title="Items",
        header=(sl.paragraph("header"),),
        footer=(sl.paragraph("footer"),),
    )
    result = plan(document, target=target(8), nav=default_nav)
    assert result.report.regions[0].pages > 1
    for index in range(result.report.regions[0].pages):
        page = plan(document, target=target(8), nav=default_nav, positions={"$/items": PagePosition(page=index)})
        texts = [node.content for node in page.scene.components_v2.children if hasattr(node, "content")]
        assert texts[0] == "header"
        assert texts[-1] == "footer"
        assert page.cost.get(Axis.COMPONENTS) <= 8


def test_authored_alternatives_participate_in_allocation() -> None:
    content = sl.fallback(sl.stack(*(sl.paragraph(str(i)) for i in range(8))), sl.paragraph("compact"))
    document = sl.region(sl.unbreakable(content), key="reference", title="Reference")
    result = plan(document, target=target(5), nav=default_nav)
    first = result.scene.components_v2.children[0]
    assert isinstance(first, scene.Text)
    assert first.content == "compact"
    assert any(event.code == "layout.semantic_fallback" for event in result.report.events)
    with pytest.raises(LayoutDegradedError):
        plan(document, target=target(5), nav=default_nav, strict=True)
    preferred = plan(document, target=target(10), nav=default_nav, strict=True)
    assert preferred.degradation.lossless
    assert len(preferred.scene.components_v2.children) == 8


def test_duplicate_identity_and_impossible_atoms_are_distinct_from_budget_exhaustion() -> None:
    with pytest.raises(LayoutInvariantError, match="duplicate sibling"):
        plan(
            sl.region(sl.paragraph("a", key="same"), sl.paragraph("b", key="same"), key="r", title="R"),
            target=target(10),
            nav=default_nav,
        )
    with pytest.raises(UnsolvableLayoutError):
        plan(
            sl.region(sl.unbreakable(sl.stack(*(sl.paragraph(str(i)) for i in range(8)))), key="r", title="R"),
            target=target(5),
            nav=default_nav,
        )
    with pytest.raises(LayoutSearchExhaustedError):
        plan(region("r", 8), target=target(8), nav=default_nav, search_budget=1)


@pytest.mark.parametrize(("left", "right", "capacity"), [(5, 5, 10), (6, 6, 11), (7, 5, 10), (4, 7, 9)])
def test_small_allocations_match_exhaustive_page_capacity_oracle(left: int, right: int, capacity: int) -> None:
    def options(count: int) -> list[tuple[int, int]]:
        return [(count, 0), *((size + 4, (count + size - 1) // size - 1) for size in range(1, count))]

    feasible = [a[1] + b[1] for a, b in product(options(left), options(right)) if a[0] + b[0] <= capacity]
    if not feasible:
        with pytest.raises(UnsolvableLayoutError):
            plan((region("left", left), region("right", right)), target=target(capacity), nav=default_nav)
    else:
        result = plan((region("left", left), region("right", right)), target=target(capacity), nav=default_nav)
        assert sum(item.pages - 1 for item in result.report.regions) == min(feasible)


class RegionScreen(sl.Component[sl.ComponentsV2Target, sd.Request]):
    def render(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        return region("left", 6), region("right", 6)


async def test_mounted_region_turns_keep_sibling_position_and_survive_failed_delivery() -> None:
    root = sd.MessageRoot(RegionScreen(), access=sd.Everyone(), target=target(10), timeout=None)
    view = commit_render(root)
    assert_within_limits(view)
    generation = root.generation
    handlers = root.snapshot().handler_keys
    allocation = root.presentation.region_layout
    failed = interaction_harness()
    failed.response.edit_message.error = http_error(503)
    with pytest.raises(discord.HTTPException):
        await root.dispatch("__cursor_next.$/left", failed, generation=generation)
    assert root.generation == generation
    assert root.snapshot().handler_keys == handlers
    assert root.presentation.region_layout == allocation
    retry = interaction_harness()
    await root.dispatch("__cursor_next.$/left", retry, generation=generation)
    assert root.presentation.cursor("$/left").position.page == 1
    assert root.presentation.cursor("$/right").position.page == 0
    assert_within_limits(retry.response.edit_message.await_args.kwargs["view"])


async def test_region_controls_use_the_existing_stale_generation_policy() -> None:
    root = sd.MessageRoot(RegionScreen(), access=sd.Everyone(), target=target(10), timeout=None)
    commit_render(root)
    generation = root.generation
    await root.dispatch("__cursor_next.$/left", interaction_harness(), generation=generation)
    await root.dispatch("__cursor_next.$/left", interaction_harness(), generation=generation)
    assert root.presentation.cursor("$/left").position.page == 1
    assert root.presentation.cursor("$/right").position.page == 0


def test_keyed_groups_can_break_without_a_second_identity_wrapper() -> None:
    document = sl.region(
        sl.stack(*(sl.paragraph(str(i), key=str(i)) for i in range(7)), key="group"), key="root", title="Root"
    )
    result = plan(document, target=target(6), nav=default_nav)
    assert result.report.regions[0].pages > 1
    assert result.report.regions[0].anchor == "group"


def test_allocation_is_committed_only_after_delivery_and_reused_for_page_turns() -> None:
    session = PresentationState()
    document = (region("left", 6), region("right", 6))
    first = plan(document, target=target(10), nav=default_nav, session=session)
    assert session.region_layout is None
    session.apply_updates(first.session_updates)
    allocation = session.region_layout
    assert allocation is not None
    for index in range(6):
        current = plan(
            document,
            target=target(10),
            nav=default_nav,
            session=session,
            positions={"$/left": PagePosition(page=index)},
        )
        session.apply_updates(current.session_updates)
        assert session.region_layout == allocation
        assert current.report.regions[1].page == 0


def test_controlled_and_routed_regions_keep_application_ownership() -> None:
    async def changed(_event: PageEvent) -> None:
        raise AssertionError("planning must not call application handlers")

    session = PresentationState()
    controlled = sl.region(
        *region("body", 7).children, key="controlled", title="Controlled", page=sl.controlled(2, changed)
    )
    result = plan(controlled, target=target(6), nav=default_nav, session=session)
    session.apply_updates(result.session_updates)
    assert result.report.regions[0].page == 2
    assert "$/controlled" not in session.cursors
    routed = sl.region(
        *region("body", 7).children, key="routed", title="Routed", page=RoutedPage(1, lambda index: f"page:{index}")
    )
    result = plan(routed, target=target(6), nav=default_nav, session=session)
    assert result.report.regions[0].page == 1
    assert not result.bindings
    assert "page:2" in scene.Codec.dumps(result.scene)


def test_sections_preserve_their_shell_around_nested_regions() -> None:
    document = sl.section(sl.heading("Details"), region("one", 7), key="details", accent=None)
    result = plan(document, target=target(8), nav=default_nav)
    panel = result.scene.components_v2.children[0]
    assert isinstance(panel, scene.Panel)
    assert isinstance(panel.children[0], scene.Text)
    assert panel.children[0].content == "## Details"
    assert result.report.regions[0].key == "$/details/one"
    assert result.cost.get(Axis.COMPONENTS) <= 8


def test_legacy_paged_content_can_participate_in_joint_allocation() -> None:
    document = sl.region(
        sl.paged(sl.stack(*(sl.paragraph("x" * 20, key=str(i)) for i in range(4))), key="legacy", chars=25),
        sl.paragraph("summary"),
        key="outer",
        title="Outer",
    )
    result = plan(document, target=target(12), nav=default_nav)
    assert [(item.key, item.pages) for item in result.report.regions] == [("$/outer", 1), ("$/outer/legacy", 4)]


def test_keep_with_next_and_unbreakable_constrain_legal_page_boundaries() -> None:
    content = sl.region(
        sl.keep_with_next(sl.paragraph("heading", key="heading")),
        sl.paragraph("body", key="body"),
        *(sl.paragraph(str(i), key=str(i)) for i in range(5)),
        key="root",
        title="Root",
    )
    result = plan(content, target=target(6), nav=default_nav)
    assert [node.content for node in result.scene.components_v2.children[:2] if isinstance(node, scene.Text)] == [
        "heading",
        "body",
    ]
    with pytest.raises(UnsolvableLayoutError):
        plan(content, target=target(5), nav=default_nav)


def test_large_root_region_finds_a_solution_with_the_default_budget() -> None:
    result = plan(region("items", 60), target=DISCORD_V2_DPY27, nav=default_nav)
    assert result.report.regions[0].pages == 2
    assert result.cost.get(Axis.COMPONENTS) <= 40


@pytest.mark.parametrize("controlled", [False, True])
def test_existing_local_pager_is_measured_across_all_pages(controlled: bool) -> None:
    async def changed(_event: PageEvent) -> None:
        raise AssertionError("measurement is read-only")

    owner = sl.controlled(0, changed) if controlled else sl.uncontrolled(0)
    lines = sl.primitives.Lines(
        ("short", "longer text"), overflow=sl.primitives.Paginate(key="local", per=1, page=owner)
    )
    document = sl.region(lines, sl.paragraph("persistent"), key="outer", title="Outer")
    first = plan(document, target=target(10), nav=default_nav)
    assert [(pager.key, pager.pages) for pager in first.scene.pagers] == [("local", 2)]
    assert first.cost.get(Axis.DISPLAY_TEXT) >= len("longer text") + len("persistent")
    if not controlled:
        second = plan(document, target=target(10), nav=default_nav, positions={"local": PagePosition(page=1)})
        assert first.cost == second.cost
        assert second.scene.pagers[0].page == 1


def test_joint_envelopes_respect_text_and_component_limits_together() -> None:
    constrained = target(16).reserve(ResourceCost({Axis.DISPLAY_TEXT: 4000 - 200}))
    document = tuple(
        sl.region(*(sl.paragraph("x" * 60, key=str(i)) for i in range(4)), key=key, title=key)
        for key in ("left", "right")
    )
    first = plan(document, target=constrained, nav=default_nav)
    assert all(report.pages > 1 for report in first.report.regions)
    for left, right in product(*(range(report.pages) for report in first.report.regions)):
        current = plan(
            document,
            target=constrained,
            nav=default_nav,
            positions={"$/left": PagePosition(page=left), "$/right": PagePosition(page=right)},
        )
        assert current.cost == first.cost
        assert current.cost.within(constrained.capacities)


def test_document_attachments_are_reserved_before_region_allocation() -> None:
    assets = tuple(sl.file(bytes([index]), filename=f"{index}.txt") for index in range(11))
    document = sl.Document((region("items", 3),), assets=assets)
    with pytest.raises(UnsolvableLayoutError, match="persistent document attachments"):
        plan(document, target=DISCORD_V2_DPY27, nav=default_nav)

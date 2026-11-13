"""Joint region allocation bounds every reachable page, including nested pagers."""

from itertools import product

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid_ui import scene
from squid_ui.errors import LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.pages import PagePosition
from squid_ui.planning import plan
from squid_ui.planning.navigation import default_nav
from squid_ui.planning.resources import Axis, ResourceCost
from squid_ui.presentation import PresentationState
from squid_ui_discord import DISCORD_V2_DPY27
from squid_ui_discord.testing import assert_within_limits, commit_render, http_error, interaction_harness


def target(components: int):
    return DISCORD_V2_DPY27.reserve(ResourceCost({Axis.COMPONENTS: 40 - components}))


def region(key: str, count: int):
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
    failed = interaction_harness()
    failed.response.edit_message.error = http_error(503)
    with pytest.raises(discord.HTTPException):
        await root.dispatch("__cursor_next.$/left", failed, generation=generation)
    assert root.generation == generation
    assert root.snapshot().handler_keys == handlers
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

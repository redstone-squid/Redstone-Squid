"""Rich native text uses legal fragments without rewriting inline identities."""

from datetime import UTC, datetime
from typing import Any

import pytest

import squid_ui as sl
from squid_i18n import tree
from squid_ui import scene
from squid_ui.content.text import NEUTRAL, Text, resolve_text, timestamp_text
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord.limits import V2Limits
from squid_ui.ext.discord.planning.discord import classic_target, components_v2_target
from squid_ui.ext.discord.primitives import nodes as native
from squid_ui.ext.discord.target_types import DiscordAdapter
from squid_ui.overflow import Paginate
from squid_ui.planning import plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.target import Target
from squid_ui.planning.text_presentations import join_text, text_fragments
from squid_ui.presentation import PresentationState

ADAPTER = AdapterProfile(DiscordAdapter, "rich-pages", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [components_v2_target(ADAPTER), classic_target(ADAPTER)]


def content_text(body: scene.Body) -> str:
    if isinstance(body, discord_scene.ClassicMessage):
        return body.content or ""
    assert isinstance(body, discord_scene.ComponentsV2)
    return next(node.content for node in body.children if isinstance(node, discord_scene.Text))


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_count_pages_preserve_styled_links_and_inline_entities(target: Target[Any, Any, Any, Any]) -> None:
    entries = (sl.strong("First"), sl.text_link("Docs", "https://example.com"), sl.mention(123456789))
    node = native.Text(Text.join("\n", entries), overflow=Paginate(key="rich", per=1))
    result = plan(node, target=target, strict=True)
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    assert len(result.allocation.regions[identity].cuts) == 3
    for index, entry in enumerate(entries):
        page = result.for_page({identity: index})
        expected = tree.markdown(resolve_text(entry, NEUTRAL).nodes, discord=True)
        assert expected in content_text(page.scene.body)
        assert not page.report.events


def test_long_styled_link_fragments_rejoin_without_literal_markdown_markers() -> None:
    label = "abcdefghij" * 8
    node = native.Text(sl.strong(sl.text_link(label, "https://example.com")), overflow=Paginate(key="rich"))
    result = plan(node, target=components_v2_target(ADAPTER, limits=V2Limits(total_text=70)), strict=True)
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    reconstructed = ""
    for index in range(len(result.allocation.regions[identity].cuts)):
        page = result.for_page({identity: index})
        content = content_text(page.scene.body)
        nodes = tree.parse((content,))
        assert len(nodes) == 1 and nodes[0].kind == "strong"
        assert len(nodes[0].children) == 1 and nodes[0].children[0].kind == "link"
        assert dict(nodes[0].children[0].attrs)["href"] == "https://example.com"
        reconstructed += tree.plain(nodes[0])
    assert reconstructed == label


@pytest.mark.parametrize(
    "entry",
    [sl.mention(123456789), sl.emoji_text("<:squid:123456789>"), timestamp_text(datetime(2026, 1, 1, tzinfo=UTC))],
)
def test_fragment_boundaries_never_split_inline_entity_identity(entry: Text) -> None:
    nodes = resolve_text(Text("before") + entry + "after", NEUTRAL).nodes
    fragments = text_fragments(nodes, 3)
    entities = [node for piece in fragments for node in piece if node.kind in ("mention", "emoji", "timestamp")]
    original = [node for node in nodes if node.kind in ("mention", "emoji", "timestamp")]
    assert entities == original
    merged: tuple[tree.Node, ...] = ()
    for fragment in fragments:
        merged = join_text(merged, fragment)
    assert tree.markdown(merged, discord=True) == tree.markdown(nodes, discord=True)


def test_count_boundary_inside_a_mention_does_not_split_its_identifier() -> None:
    result = plan(
        native.Text(sl.mention(123456789), overflow=Paginate(key="rich", boundary="4", per=1)), target=TARGETS[0]
    )
    assert content_text(result.scene.body) == "<@123456789>"
    assert not result.scene.pagers


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_rich_code_pages_repeat_fences_and_keep_literal_source(target: Target[Any, Any, Any, Any]) -> None:
    node = native.Code(sl.strong("print(1)\nprint(2)"), lang="py", overflow=Paginate(key="code", per=1))
    result = plan(node, target=target)
    assert result.allocation is not None
    identity = next(iter(result.allocation.regions))
    for index in range(2):
        content = content_text(result.for_page({identity: index}).scene.body)
        assert f"```py\nprint({index + 1})\n```" in content
        assert "**" not in content


async def ignore_turn(event: sl.PageEvent) -> None:
    pass


def test_controlled_rich_pages_reuse_allocation_with_the_current_owner_value() -> None:
    session = PresentationState()
    content = sl.strong("First\nSecond\nThird")

    def node(page: int) -> native.Text:
        return native.Text(content, overflow=Paginate(key="rich", per=1, page=sl.controlled(page, ignore_turn)))

    first = plan(node(0), target=TARGETS[0], session=session)
    session.apply_updates(first.session_updates)
    second = plan(node(1), target=TARGETS[0], session=session)
    assert second.allocation is first.allocation
    assert content_text(second.scene.body) == "**Second**"
    assert "rich" not in session.cursors

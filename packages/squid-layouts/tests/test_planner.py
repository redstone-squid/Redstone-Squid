"""Logical planning and mechanical Discord drawing."""

import discord
import pytest

from squid_layouts import (
    LIMITS,
    ActionGroup,
    Button,
    Choice,
    LayoutInvariantError,
    Lines,
    Paginate,
    Panel,
    Row,
    SceneCodec,
    Section,
    TargetProfile,
    Text,
    Thumbnail,
    UnsolvableLayoutError,
    Variant,
    plan,
)
from squid_layouts.discord import DISCORD_V2, DiscordRenderer, NativeItem
from squid_layouts.scene import SceneRow, SceneText


async def _click(event) -> None: ...


def test_planner_extracts_callbacks_from_the_serializable_scene() -> None:
    result = plan(
        Panel((Text("hello"), Row((Button(label="Act", on_click=_click, key="act"),)))),
        target=DISCORD_V2,
    )

    assert result.bindings["act"].handler is _click
    encoded = SceneCodec.dumps(result.scene)
    assert "_click" not in encoded
    assert '"action":"act"' in encoded


def test_duplicate_action_keys_fail_before_drawing() -> None:
    with pytest.raises(LayoutInvariantError, match="duplicate action key"):
        plan(
            Row(
                (
                    Button(label="One", on_click=_click, key="same"),
                    Button(label="Two", on_click=_click, key="same"),
                )
            ),
            target=DISCORD_V2,
        )


def test_static_discord_renderer_matches_scene_structure() -> None:
    result = plan(Panel((Text("hello"),)), target=DISCORD_V2)
    view = DiscordRenderer().draw(result.scene, plan=result)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.to_components()[0]["type"] == 17


def test_action_group_chunks_controls_without_dropping_any() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    result = plan(ActionGroup(buttons), target=DISCORD_V2)

    rows = [node for node in result.scene.children if isinstance(node, SceneRow)]
    assert [len(row.items) for row in rows] == [5, 1]
    assert set(result.bindings) == {f"b{index}" for index in range(6)}


def test_exact_row_overflow_is_a_typed_planning_error() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    with pytest.raises(LayoutInvariantError, match="row has 6 controls"):
        plan(Row(buttons), target=DISCORD_V2)


def test_planner_requires_explicit_unique_pager_keys() -> None:
    with pytest.raises(LayoutInvariantError, match="requires an explicit key"):
        plan(Text("content", overflow=Paginate()), target=DISCORD_V2)

    with pytest.raises(LayoutInvariantError, match="duplicate pager key 'results'"):
        plan(
            (
                Lines(("one",), overflow=Paginate(key="results", per=1)),
                Lines(("two",), overflow=Paginate(key="results", per=1)),
            ),
            target=DISCORD_V2,
        )


def test_planner_rejects_pagination_inside_a_section() -> None:
    section = Section(
        (Text("content", overflow=Paginate(key="detail")),),
        Thumbnail("https://example.invalid/image.png"),
    )

    with pytest.raises(LayoutInvariantError, match="cannot be nested in a Section"):
        plan(section, target=DISCORD_V2)


def test_scene_reports_every_independent_pager() -> None:
    result = plan(
        (
            Lines(tuple(f"left {index}" for index in range(4)), overflow=Paginate(key="left", per=2)),
            Lines(tuple(f"right {index}" for index in range(6)), overflow=Paginate(key="right", per=2)),
        ),
        target=DISCORD_V2,
        page={"left": 1, "right": 2},
    )

    assert [(pager.key, pager.page, pager.pages) for pager in result.scene.pagers] == [
        ("left", 1, 2),
        ("right", 2, 3),
    ]


def test_choice_selects_by_capability_before_budget_degradation() -> None:
    choice = Choice(
        (
            Variant(Text("rich"), requires=frozenset({"rich-text"})),
            Variant(Text("plain")),
        )
    )
    basic = TargetProfile("test", 1, limits=LIMITS)
    rich = TargetProfile("test", 1, capabilities=frozenset({"rich-text"}), limits=LIMITS)

    basic_scene = plan(choice, target=basic).scene
    rich_scene = plan(choice, target=rich).scene

    assert basic_scene.children == (SceneText("plain"),)
    assert rich_scene.children == (SceneText("rich"),)


def test_native_item_is_built_once_measured_recursively_and_reused() -> None:
    calls = 0
    native = discord.ui.Container(*(discord.ui.TextDisplay(str(index)) for index in range(39)))

    def factory() -> discord.ui.Item:
        nonlocal calls
        calls += 1
        return native

    result = plan(NativeItem(factory, fallback=Text("fallback")), target=DISCORD_V2)
    view = DiscordRenderer().draw(result.scene, plan=result)

    assert calls == 1
    assert view.children[0] is native


def test_native_nested_component_cost_can_make_a_document_unsolvable() -> None:
    native = NativeItem(
        lambda: discord.ui.Container(*(discord.ui.TextDisplay(str(index)) for index in range(39))),
        fallback=Text("fallback"),
    )

    with pytest.raises(UnsolvableLayoutError, match="41 components"):
        plan((native, Text("outside")), target=DISCORD_V2)


def test_unsupported_native_extension_uses_its_portable_fallback_without_building() -> None:
    called = False

    def factory() -> discord.ui.Item:
        nonlocal called
        called = True
        return discord.ui.TextDisplay("native")

    target = TargetProfile("portable.test", 1, limits=LIMITS)
    result = plan(NativeItem(factory, fallback=Text("portable")), target=target)

    assert result.scene.children == (SceneText("portable"),)
    assert not called

"""Logical planning and mechanical Discord drawing."""

from datetime import UTC, datetime

import discord
import pytest

from squid_i18n import tr
from squid_ui import Document, paragraph, scene, zoned_timestamp
from squid_ui.authoring.document import Asset, InlineAsset
from squid_ui.authoring.presentations import corresponds
from squid_ui.content.temporal import ZonedDateTime
from squid_ui.content.text import Localization, Markup
from squid_ui.errors import LayoutInvariantError, UnsolvableLayoutError
from squid_ui.ext.discord import DISCORD_V2_DPY27
from squid_ui.ext.discord import scene as discord_scene
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.primitives import (
    Button,
    Code,
    ControlGroup,
    Lines,
    Never,
    Paginate,
    Panel,
    Row,
    Section,
    Text,
    Thumbnail,
    Variant,
    Variants,
)
from squid_ui.ext.discord.rendering.v2 import V2Renderer
from squid_ui.ext.discord.target import NativeItem
from squid_ui.ext.html.capabilities import HtmlAdapterCapability
from squid_ui.pages import PagePosition
from squid_ui.planning import plan
from squid_ui.planning.joint_search import JointInfeasibleError, JointUnsupportedError


async def _click(event) -> None: ...


def _nav(context):
    return (
        Row(
            (
                Button("Previous", _click, f"prev.{context.key}", disabled=not context.has_previous),
                Button("Next", _click, f"next.{context.key}", disabled=not context.has_next),
            )
        ),
    )


def test_planner_extracts_callbacks_from_the_serializable_scene() -> None:
    result = plan(
        Panel((Text("hello"), Row((Button(label="Act", on_click=_click, key="act"),)))),
        target=DISCORD_V2_DPY27,
    )

    assert result.bindings["act"].handler is _click
    encoded = scene.Codec.dumps(result.scene)
    assert "_click" not in encoded
    assert '"action":"act"' in encoded


def test_planner_resolves_deferred_text_on_exact_primitives() -> None:
    localization = Localization("fr", ("test-hello = Bonjour",))

    result = plan(Text(tr(t"test-hello = Hello")), target=DISCORD_V2_DPY27, localization=localization)

    assert result.scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("Bonjour", markup=Markup.DISCORD_MARKDOWN),
    )


def test_duplicate_action_keys_fail_before_drawing() -> None:
    with pytest.raises(LayoutInvariantError, match="duplicate semantic identity"):
        plan(
            Row(
                (
                    Button(label="One", on_click=_click, key="same"),
                    Button(label="Two", on_click=_click, key="same"),
                )
            ),
            target=DISCORD_V2_DPY27,
        )


def test_static_discord_renderer_matches_scene_structure() -> None:
    result = plan(Panel((Text("hello"),)), target=DISCORD_V2_DPY27)
    view = V2Renderer().view(result.scene, plan=result)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.to_components()[0]["type"] == 17


def test_discord_renderer_draws_zoned_timestamp_in_its_named_zone() -> None:
    value = ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")
    result = plan(zoned_timestamp(value, label="Starts"), target=DISCORD_V2_DPY27)

    view = V2Renderer().view(result.scene, plan=result)

    displays = [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
    assert displays == ["**Starts:** 2026-08-22 10:30:00-04:00[America/New_York]"]


def test_assets_are_scene_resources_not_visual_children() -> None:
    asset = Asset("report", "report.txt", "text/plain", InlineAsset(b"full report"))
    result = plan(Document((Text("summary"),), (asset,)), target=DISCORD_V2_DPY27)

    assert result.scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("summary", markup=Markup.DISCORD_MARKDOWN),
    )
    assert result.scene.assets[0].key == "report"
    assert result.resources["asset:report"] is asset


def test_action_group_chunks_controls_without_dropping_any() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    result = plan(ControlGroup(buttons), target=DISCORD_V2_DPY27)

    rows = [
        node
        for node in result.scene.expect_body(discord_scene.ComponentsV2).children
        if isinstance(node, discord_scene.Row)
    ]
    assert [len(row.items) for row in rows] == [5, 1]
    assert set(result.bindings) == {f"b{index}" for index in range(6)}


def test_explicit_document_key_allows_lossless_root_component_paging() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))
    document = Document((ControlGroup(buttons),), key="toolbar")
    first = plan(document, target=DISCORD_V2_DPY27, nav=_nav)

    assert first.scene.pagers[0].key == "toolbar"
    assert first.scene.pagers[0].pages > 1
    assert first.report.search is not None
    assert first.allocation is not None

    visible: set[str] = set()
    for page_index in range(first.scene.pagers[0].pages):
        page_result = plan(
            document,
            target=DISCORD_V2_DPY27,
            nav=_nav,
            positions={"toolbar": PagePosition(page=page_index)},
        )
        visible.update(key for key in page_result.bindings if key.startswith("b"))
    assert visible == {f"b{index}" for index in range(41)}


def test_a_cosmetic_note_does_not_fragment_root_pages() -> None:
    """A one-segment local pager must not add a visible page turn to the toolbar."""
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))

    def pages_for(overflow) -> int:
        document = Document((Text("hi", overflow=overflow), ControlGroup(buttons)), key="toolbar")
        return plan(document, target=DISCORD_V2_DPY27, nav=_nav).scene.pagers[0].pages

    assert pages_for(Paginate(key="noted", per=5)) == pages_for(Never())


def test_root_paging_requires_an_explicit_document_key() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))

    with pytest.raises(JointInfeasibleError) as caught:
        plan(ControlGroup(buttons), target=DISCORD_V2_DPY27, nav=_nav)
    assert caught.value.diagnostics.violations


def test_local_pagination_and_root_pagination_share_allocation() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))
    document = Document(
        (ControlGroup(buttons), Code("x" * 9000, overflow=Paginate(key="detail"))),
        key="root",
    )

    result = plan(document, target=DISCORD_V2_DPY27, nav=_nav)
    assert result.allocation is not None
    assert {region.cursor_key for region in result.allocation.regions.values()} == {"root", "detail"}
    root, detail = (
        next(identity for identity, region in result.allocation.regions.items() if region.cursor_key == key)
        for key in ("root", "detail")
    )
    visible: set[str] = set()
    code: list[str] = []
    for root_page in range(len(result.allocation.regions[root].cuts)):
        for detail_page in range(len(result.allocation.regions[detail].cuts)):
            page = result.allocation.select({root: root_page, detail: detail_page})
            visible.update(key for key in page.bindings if key.startswith("b"))
            code.extend(
                node.content[4:-4]
                for node in page.scene.expect_body(discord_scene.ComponentsV2).children
                if isinstance(node, discord_scene.Text) and node.content.startswith("```\n")
            )
    assert visible == {f"b{index}" for index in range(41)}
    assert "".join(code) == "x" * 9000


def test_exact_row_overflow_is_a_typed_planning_error() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    with pytest.raises(JointInfeasibleError):
        plan(Row(buttons), target=DISCORD_V2_DPY27)


def test_planner_accepts_positional_pagers_but_rejects_duplicate_authored_keys() -> None:
    result = plan(Text("content", overflow=Paginate()), target=DISCORD_V2_DPY27)
    assert result.allocation is not None
    assert next(iter(result.allocation.regions.values())).cursor_key.startswith("$/")

    with pytest.raises(LayoutInvariantError, match="duplicate semantic identity"):
        plan(
            (
                Lines(("one",), overflow=Paginate(key="results", per=1)),
                Lines(("two",), overflow=Paginate(key="results", per=1)),
            ),
            target=DISCORD_V2_DPY27,
        )


def test_planner_rejects_pagination_inside_a_section() -> None:
    section = Section(
        (Text("content", overflow=Paginate(key="detail")),),
        Thumbnail("https://example.invalid/image.png"),
    )

    with pytest.raises(JointUnsupportedError):
        plan(section, target=DISCORD_V2_DPY27)


def test_scene_reports_every_independent_pager() -> None:
    result = plan(
        (
            Lines(tuple(f"left {index}" for index in range(4)), overflow=Paginate(key="left", per=2)),
            Lines(tuple(f"right {index}" for index in range(6)), overflow=Paginate(key="right", per=2)),
        ),
        target=DISCORD_V2_DPY27,
        positions={"left": PagePosition(page=1), "right": PagePosition(page=2)},
    )

    assert [(pager.key, pager.page, pager.pages) for pager in result.scene.pagers] == [
        ("left", 1, 2),
        ("right", 2, 3),
    ]


def test_a_ladder_selects_by_capability_before_budget_degradation() -> None:
    ladder = Variants(
        (
            Variant((Text("rich"),), requires=frozenset({HtmlAdapterCapability.RENDER_HTML})),
            Variant((Text("plain"),)),
        )
    )
    basic = sd.target_profile("test")
    rich = sd.target_profile("rich", capabilities=frozenset({HtmlAdapterCapability.RENDER_HTML}))

    basic_scene = plan(ladder, target=basic).scene
    rich_scene = plan(ladder, target=rich).scene

    assert basic_scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("plain", markup=Markup.DISCORD_MARKDOWN),
    )
    assert rich_scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("rich", markup=Markup.DISCORD_MARKDOWN),
    )


def test_capability_filtering_shortens_the_ladder_the_solver_steps() -> None:
    """An unsupported rung is gone before stepping, not skipped over during it."""

    def rung(index: int, texts: int) -> Panel:
        return Panel(children=tuple(Text(f"n{index}.{step}") for step in range(texts)))

    # Rung 0 needs a capability the target lacks, so each ladder opens on rung 1 already.
    # Nine surviving rung 1s cost 45 against a ceiling of 40, so stepping still has work.
    ladders = [
        Variants(
            (
                Variant(
                    (rung(index, 6),),
                    requires=frozenset({"rich-text"}),
                    correspondence=(corresponds("@content", "source"),),
                ),
                Variant((rung(index, 4),), correspondence=(corresponds("@content", "source"),)),
                Variant((Text(f"line {index}"),), correspondence=(corresponds("@content", "source"),)),
            ),
            content=paragraph(f"Fixture {index}", key="source"),
        )
        for index in range(9)
    ]
    document = plan(ladders, target=sd.target_profile("test")).scene
    rendered = repr(document.expect_body(discord_scene.ComponentsV2).children)

    assert "n0.5" not in rendered  # the gated rung never reaches the solver
    assert "line 0" in rendered  # the ladder still had its last rung to step to
    assert "n8.0" in rendered  # and stepping stopped once the document fit


def test_native_item_is_built_once_measured_recursively_and_reused() -> None:
    calls = 0
    native = discord.ui.Container(*(discord.ui.TextDisplay(str(index)) for index in range(39)))

    def factory() -> discord.ui.Item:
        nonlocal calls
        calls += 1
        return native

    result = plan(NativeItem(factory, fallback=Text("fallback")), target=DISCORD_V2_DPY27)
    view = V2Renderer().view(result.scene, plan=result)

    assert calls == 1
    assert view.children[0] is native


def test_native_nested_component_cost_can_make_a_document_unsolvable() -> None:
    native = NativeItem(
        lambda: discord.ui.Container(*(discord.ui.TextDisplay(str(index)) for index in range(39))),
        fallback=Text("fallback"),
    )

    with pytest.raises(UnsolvableLayoutError, match="no declared joint allocation"):
        plan((native, Text("outside", overflow=Never())), target=DISCORD_V2_DPY27)


def test_unsupported_native_extension_uses_its_portable_fallback_without_building() -> None:
    called = False

    def factory() -> discord.ui.Item:
        nonlocal called
        called = True
        return discord.ui.TextDisplay("native")

    target = sd.target_profile("portable.test")
    result = plan(NativeItem(factory, fallback=Text("portable")), target=target)

    assert result.scene.expect_body(discord_scene.ComponentsV2).children == (
        discord_scene.Text("portable", markup=Markup.DISCORD_MARKDOWN),
    )
    assert not called

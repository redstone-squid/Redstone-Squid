"""Logical planning and mechanical Discord drawing."""

from datetime import UTC, datetime

import discord
import pytest

from squid_layouts import (
    Asset,
    Document,
    InlineAsset,
    LayoutInvariantError,
    Localization,
    Message,
    Position,
    UnsolvableLayoutError,
    ZonedDateTime,
    plan,
    zoned_timestamp,
)
from squid_layouts.discord import DEFAULT_LIMITS as LIMITS
from squid_layouts.discord import DEFAULT_TARGET, NativeItem, Renderer
from squid_layouts.planning import TargetProfile
from squid_layouts.primitives import (
    ActionGroup,
    Button,
    Code,
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
from squid_layouts.scene import Codec as SceneCodec
from squid_layouts.scene.model import SceneRow, SceneText


async def _click(event) -> None: ...


def _nav(state):
    return (
        Row(
            (
                Button("Previous", _click, f"prev.{state.key}", disabled=not state.has_previous),
                Button("Next", _click, f"next.{state.key}", disabled=not state.has_next),
            )
        ),
    )


def test_planner_extracts_callbacks_from_the_serializable_scene() -> None:
    result = plan(
        Panel((Text("hello"), Row((Button(label="Act", on_click=_click, key="act"),)))),
        target=DEFAULT_TARGET,
    )

    assert result.bindings["act"].handler is _click
    encoded = SceneCodec.dumps(result.scene)
    assert "_click" not in encoded
    assert '"action":"act"' in encoded


def test_planner_resolves_deferred_text_on_exact_primitives() -> None:
    localization = Localization("xx", gettext=lambda message: {"Hello": "Bonjour"}[message])

    result = plan(Text(Message("Hello")), target=DEFAULT_TARGET, localization=localization)

    assert result.scene.children == (SceneText("Bonjour"),)


def test_duplicate_action_keys_fail_before_drawing() -> None:
    with pytest.raises(LayoutInvariantError, match="duplicate action key"):
        plan(
            Row(
                (
                    Button(label="One", on_click=_click, key="same"),
                    Button(label="Two", on_click=_click, key="same"),
                )
            ),
            target=DEFAULT_TARGET,
        )


def test_static_discord_renderer_matches_scene_structure() -> None:
    result = plan(Panel((Text("hello"),)), target=DEFAULT_TARGET)
    view = Renderer().draw(result.scene, plan=result)

    assert isinstance(view, discord.ui.LayoutView)
    assert view.to_components()[0]["type"] == 17


def test_discord_renderer_draws_zoned_timestamp_in_its_named_zone() -> None:
    value = ZonedDateTime(datetime(2026, 8, 22, 14, 30, tzinfo=UTC), "America/New_York")
    result = plan(zoned_timestamp(value, label="Starts"), target=DEFAULT_TARGET)

    view = Renderer().draw(result.scene, plan=result)

    displays = [item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
    assert displays == ["**Starts:** 2026-08-22 10:30:00-04:00[America/New_York]"]


def test_assets_are_scene_resources_not_visual_children() -> None:
    asset = Asset("report", "report.txt", "text/plain", InlineAsset(b"full report"))
    result = plan(Document((Text("summary"),), (asset,)), target=DEFAULT_TARGET)

    assert result.scene.children == (SceneText("summary"),)
    assert result.scene.assets[0].key == "report"
    assert result.resources["asset:report"] is asset


def test_action_group_chunks_controls_without_dropping_any() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    result = plan(ActionGroup(buttons), target=DEFAULT_TARGET)

    rows = [node for node in result.scene.children if isinstance(node, SceneRow)]
    assert [len(row.items) for row in rows] == [5, 1]
    assert set(result.bindings) == {f"b{index}" for index in range(6)}


def test_explicit_document_key_allows_lossless_root_component_paging() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))
    document = Document((ActionGroup(buttons),), key="toolbar")
    first = plan(document, target=DEFAULT_TARGET, nav=_nav)

    assert first.scene.pagers[0].key == "toolbar"
    assert first.scene.pagers[0].pages > 1
    assert first.report.events[0].code == "pagination.root"

    visible: set[str] = set()
    for page_index in range(first.scene.pagers[0].pages):
        page_result = plan(
            document,
            target=DEFAULT_TARGET,
            nav=_nav,
            positions={"toolbar": Position(offset=page_index)},
        )
        visible.update(key for key in page_result.bindings if key.startswith("b"))
    assert visible == {f"b{index}" for index in range(41)}


def test_a_cosmetic_note_does_not_fragment_root_pages() -> None:
    """Cutting on any note put every later node on a page of its own.

    `Paginate(per=...)` on a node that is not `Lines` is noted and ignored — the content
    is unchanged and nothing had to give — but the note was sticky across every probe
    that included the node, so each following node looked like it no longer fitted.
    """
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))

    def pages_for(overflow) -> int:
        document = Document((Text("hi", overflow=overflow), ActionGroup(buttons)), key="toolbar")
        return plan(document, target=DEFAULT_TARGET, nav=_nav).scene.pagers[0].pages

    assert pages_for(Paginate(key="noted", per=5)) == pages_for(Never())


def test_root_paging_requires_an_explicit_document_key() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))

    with pytest.raises(UnsolvableLayoutError, match="give Document an explicit key"):
        plan(ActionGroup(buttons), target=DEFAULT_TARGET, nav=_nav)


def test_local_pagination_precedes_root_pagination_instead_of_nesting() -> None:
    buttons = tuple(Button(str(index), _click, f"b{index}") for index in range(41))
    document = Document(
        (ActionGroup(buttons), Code("x" * 9000, overflow=Paginate(key="detail"))),
        key="root",
    )

    with pytest.raises(UnsolvableLayoutError, match="Local and root pagination are never simultaneous"):
        plan(document, target=DEFAULT_TARGET, nav=_nav)


def test_exact_row_overflow_is_a_typed_planning_error() -> None:
    buttons = tuple(Button(label=str(index), on_click=_click, key=f"b{index}") for index in range(6))
    with pytest.raises(LayoutInvariantError, match="row has 6 controls"):
        plan(Row(buttons), target=DEFAULT_TARGET)


def test_planner_requires_explicit_unique_pager_keys() -> None:
    with pytest.raises(LayoutInvariantError, match="requires an explicit key"):
        plan(Text("content", overflow=Paginate()), target=DEFAULT_TARGET)

    with pytest.raises(LayoutInvariantError, match="duplicate pager key 'results'"):
        plan(
            (
                Lines(("one",), overflow=Paginate(key="results", per=1)),
                Lines(("two",), overflow=Paginate(key="results", per=1)),
            ),
            target=DEFAULT_TARGET,
        )


def test_planner_rejects_pagination_inside_a_section() -> None:
    section = Section(
        (Text("content", overflow=Paginate(key="detail")),),
        Thumbnail("https://example.invalid/image.png"),
    )

    with pytest.raises(LayoutInvariantError, match="cannot be nested in a Section"):
        plan(section, target=DEFAULT_TARGET)


def test_scene_reports_every_independent_pager() -> None:
    result = plan(
        (
            Lines(tuple(f"left {index}" for index in range(4)), overflow=Paginate(key="left", per=2)),
            Lines(tuple(f"right {index}" for index in range(6)), overflow=Paginate(key="right", per=2)),
        ),
        target=DEFAULT_TARGET,
        positions={"left": Position(offset=1), "right": Position(offset=2)},
    )

    assert [(pager.key, pager.page, pager.pages) for pager in result.scene.pagers] == [
        ("left", 1, 2),
        ("right", 2, 3),
    ]


def test_a_ladder_selects_by_capability_before_budget_degradation() -> None:
    ladder = Variants(
        (
            Variant((Text("rich"),), requires=frozenset({"rich-text"})),
            Variant((Text("plain"),)),
        )
    )
    basic = TargetProfile("test", 1, limits=LIMITS)
    rich = TargetProfile("test", 1, capabilities=frozenset({"rich-text"}), limits=LIMITS)

    basic_scene = plan(ladder, target=basic).scene
    rich_scene = plan(ladder, target=rich).scene

    assert basic_scene.children == (SceneText("plain"),)
    assert rich_scene.children == (SceneText("rich"),)


def test_capability_filtering_shortens_the_ladder_the_solver_steps() -> None:
    """An unsupported rung is gone before stepping, not skipped over during it."""

    def rung(index: int, texts: int) -> Panel:
        return Panel(children=tuple(Text(f"n{index}.{step}") for step in range(texts)))

    # Rung 0 needs a capability the target lacks, so each ladder opens on rung 1 already.
    # Nine surviving rung 1s cost 45 against a ceiling of 40, so stepping still has work.
    ladders = [
        Variants(
            (
                Variant((rung(index, 6),), requires=frozenset({"rich-text"})),
                Variant((rung(index, 4),)),
                Variant((Text(f"line {index}"),)),
            )
        )
        for index in range(9)
    ]
    scene = plan(ladders, target=TargetProfile("test", 1, limits=LIMITS)).scene
    rendered = repr(scene.children)

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

    result = plan(NativeItem(factory, fallback=Text("fallback")), target=DEFAULT_TARGET)
    view = Renderer().draw(result.scene, plan=result)

    assert calls == 1
    assert view.children[0] is native


def test_native_nested_component_cost_can_make_a_document_unsolvable() -> None:
    native = NativeItem(
        lambda: discord.ui.Container(*(discord.ui.TextDisplay(str(index)) for index in range(39))),
        fallback=Text("fallback"),
    )

    with pytest.raises(UnsolvableLayoutError, match="41 components"):
        plan((native, Text("outside")), target=DEFAULT_TARGET)


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

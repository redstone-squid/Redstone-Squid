"""Native semantic planning for the first-class HTML target."""

from datetime import UTC, date, datetime, time
from typing import Any, cast

import pytest

import squid_ui as sl
from squid_ui import scene, testing
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError
from squid_ui.forms import (
    BoolField,
    ChoiceField,
    ChoiceOption,
    DateField,
    DateTimeField,
    DurationField,
    ExtensionField,
    FloatField,
    FormSpec,
    FormText,
    IntField,
    MultiChoiceField,
    ScaleField,
    TextAreaField,
    TextField,
    TimeField,
    ZonedDateTimeField,
)
from squid_ui.interactions import ActionEvent, SelectionEvent, SubmitEvent
from squid_ui.planning import PlanCache, PlanMemo, ResourceCost
from squid_ui.planning.limits import Axis
from squid_ui.rosters import RosterEntry, RosterSlot, place_roster
from squid_ui.runtime.presentation_state import PresentationState, SelectionState
from squid_ui.sources import Position
from squid_ui.target_types import HtmlTarget, Renderable
from squid_ui.temporal import ZonedDateTime


async def _pressed(_event: ActionEvent) -> None:
    return None


async def _selected(_event: SelectionEvent) -> None:
    return None


async def _submitted(_event: SubmitEvent) -> None:
    return None


def _elements(result: scene.PlanResult[scene.HtmlBody]) -> tuple[scene.HtmlElement, ...]:
    return testing.find_all(result.scene.body.children, scene.HtmlElement)


def test_html_target_has_an_unbounded_semantic_identity() -> None:
    target = sl.html.target()

    assert target.triple == "html.semantic+squid-ui.html"
    assert target.capacities == {}
    assert target.body_type is scene.HtmlBody
    assert sl.planning.plan(sl.paragraph("portable"), target=target).scene.target == "html.semantic"

    with pytest.raises(LayoutInvariantError, match="no reservable"):
        sl.planning.plan(sl.paragraph("x"), target=target, reservation=ResourceCost({Axis.COMPONENTS: 1}))


def test_discord_primitives_are_rejected_by_html_planning() -> None:
    with pytest.raises(LayoutInvariantError, match=r"Discord-shaped primitive Text.*fallback\(\)"):
        sl.planning.plan(sl.primitives.Text("exact"), target=sl.html.target())  # type: ignore[arg-type]


def test_unknown_renderables_are_rejected_by_html_planning() -> None:
    class Unregistered(Renderable[HtmlTarget]):
        pass

    # A Renderable subclass nobody taught the vocabulary about is refused by name, not
    # misdescribed as a Discord primitive.
    with pytest.raises(LayoutInvariantError, match="Unregistered is not a semantic layout node"):
        sl.planning.plan(cast(Any, Unregistered()), target=sl.html.target())


def test_html_planner_preserves_semantic_structures_and_metadata() -> None:
    placement = place_roster(
        (RosterEntry("1", "Ada", "builders"),),
        (RosterSlot("builders", "Builders", 2),),
    )
    instant = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    document = sl.Document(
        (
            sl.group(
                sl.section(
                    sl.heading("Overview"),
                    sl.paragraph("Summary"),
                    sl.bullets("One", "Two", key="bullets"),
                    sl.fields(sl.field("Author", "Ada"), sl.field("Version", "1")),
                    sl.table(
                        sl.columns(sl.column("Name"), sl.column("Value")),
                        sl.table_row("Door", "Fast"),
                        key="builds",
                    ),
                    sl.quote("Signal", attribution="Observer"),
                    sl.code("give @s stone", language="mcfunction"),
                    sl.figure(
                        sl.media_item("https://example.invalid/door.png", description="Door"),
                        caption="Preview",
                    ),
                    sl.media(
                        sl.media_item("https://example.invalid/one.png", description="One"),
                        sl.media_item("https://example.invalid/two.png", description="Two"),
                        key="gallery",
                    ),
                    sl.details(sl.summary("More"), sl.paragraph("Details"), key="details"),
                    sl.status("Healthy", tone=sl.Tone.SUCCESS),
                    sl.progress(3, maximum=4, label="Complete"),
                    sl.metric(4, "Ticks", unit="gt"),
                    sl.timestamp(instant, label="Updated"),
                    sl.zoned_timestamp(ZonedDateTime(instant, "Europe/Berlin"), label="Local"),
                    thumbnail="https://example.invalid/thumb.png",
                ),
                sl.aside(sl.note("Caveat"), tone=sl.Tone.WARNING),
                sl.roster(placement, key="roster", on_join=_selected),
                sl.grid(
                    sl.grids.GridCell("a", "A"),
                    sl.grids.GridCell("b", "B", available=False),
                    columns=2,
                    key="grid",
                    on_pick=_selected,
                ),
                sl.items(
                    sl.item(sl.item_label("First"), sl.paragraph("First body"), key="first"),
                    sl.item(sl.item_label("Second"), sl.paragraph("Second body"), key="second"),
                    key="items",
                ),
                sl.navigation(
                    sl.nav_option("Overview", key="overview"),
                    sl.nav_option("History", key="history"),
                    key="nav",
                ),
            ),
            sl.action_controls(
                sl.action_control("Save", _pressed, key="save"),
                sl.link("Docs", "https://example.invalid/docs", key="docs"),
                sl.routed_action_control("Durable", "build:1:open", key="open"),
                key="actions",
            ),
            sl.choices(
                *(sl.choice(f"Choice {index}", key=str(index)) for index in range(30)),
                key="choice",
                maximum=3,
            ),
            sl.routed_choices(
                sl.choice("One", key="one"),
                sl.choice("Two", key="two"),
                key="route-choice",
                route_id="build:1:choice",
            ),
        ),
        key="semantic",
    )

    result = sl.planning.plan(document, target=sl.html.target(), localization=sl.text.Localization("en-GB"))
    elements = _elements(result)
    tags = {element.tag for element in elements}

    assert {
        scene.HtmlTag.SECTION,
        scene.HtmlTag.ASIDE,
        scene.HtmlTag.TABLE,
        scene.HtmlTag.BLOCKQUOTE,
        scene.HtmlTag.CODE,
        scene.HtmlTag.FIGURE,
        scene.HtmlTag.DETAILS,
        scene.HtmlTag.PROGRESS,
        scene.HtmlTag.TIME,
        scene.HtmlTag.NAV,
    } <= tags
    choice = next(
        element
        for element in elements
        if element.tag is scene.HtmlTag.SELECT and any(attribute.value == "choice" for attribute in element.attributes)
    )
    assert len(choice.children) == 30
    assert result.scene.body.locale == "en-GB"
    assert {"save", "choice", "details.toggle", "grid.a"} <= set(result.bindings)
    assert any(element.route == scene.HtmlRouteRef("build:1:open") for element in elements)
    assert any(element.route == scene.HtmlRouteRef("build:1:choice") for element in elements)


class _PortableExtension(ExtensionField[str]):
    capability = "forms.test.native"


def test_html_planner_renders_portable_forms_inline_with_prefill() -> None:
    spec = FormSpec(
        "Edit build",
        (
            FormText("All fields are native"),
            TextField("Name", "name", placeholder="Door", minimum=2, maximum=80),
            TextAreaField("Notes", "notes"),
            IntField("Ticks", "ticks", minimum=1, maximum=20),
            FloatField("Rate", "rate", minimum=0.0, maximum=1.0),
            DurationField("Delay", "delay"),
            DateField("Day", "day", minimum=date(2026, 1, 1)),
            TimeField("Time", "time", minimum=time(8, 0)),
            DateTimeField("Instant", "instant"),
            ZonedDateTimeField("Local", "local", timezone="Europe/Berlin"),
            ScaleField("Rating", "rating", minimum=1, maximum=5),
            ChoiceField("Mode", "mode", options=(ChoiceOption("fast", "Fast", "fast"),)),
            MultiChoiceField(
                "Tags",
                "tags",
                options=(ChoiceOption("door", "Door", "door"), ChoiceOption("logic", "Logic", "logic")),
                maximum=2,
            ),
            BoolField("Published", "published", required=False),
            _PortableExtension(
                "Portable",
                "portable",
                fallback=TextField(label="Portable fallback", key="portable"),
            ),
        ),
        prefill={"name": '"><script>alert(1)</script>', "tags": ("door",), "published": True},
    )

    result = sl.planning.plan(sl.form("Save", spec, key="edit", on_submit=_submitted), target=sl.html.target())
    elements = _elements(result)
    controls = [
        element
        for element in elements
        if element.tag in {scene.HtmlTag.INPUT, scene.HtmlTag.TEXTAREA, scene.HtmlTag.SELECT}
    ]
    names = {
        attribute.value
        for element in controls
        for attribute in element.attributes
        if attribute.name is scene.HtmlAttributeName.NAME
    }

    assert set(spec.field_keys) == names
    portable = result.form_bindings["edit"].spec.items[-1]
    assert isinstance(portable, TextField)
    assert portable.label == "Portable fallback"
    assert result.bindings["edit"].mode.value == "exclusive"
    assert all(element.form is not None for element in controls)
    published = next(
        element for element in controls if any(attribute.value == "published" for attribute in element.attributes)
    )
    assert any(attribute.name is scene.HtmlAttributeName.CHECKED for attribute in published.attributes)


def test_html_only_applies_authored_budgets_and_paging() -> None:
    unconstrained = sl.planning.plan(sl.paragraph("x" * 8000), target=sl.html.target())
    assert (
        sum(
            len(node.content)
            for node in testing.walk(unconstrained.scene.body.children)
            if isinstance(node, scene.HtmlText)
        )
        == 8000
    )
    assert unconstrained.scene.pagers == ()

    constrained = sl.planning.plan(
        sl.budget(sl.paragraph("abcdefghij"), min=2, prefer=4),
        target=sl.html.target(),
    )
    assert any(event.code == "html.budget.truncated" for event in constrained.report.events)
    assert "abcd" in [
        node.content for node in testing.walk(constrained.scene.body.children) if isinstance(node, scene.HtmlText)
    ]
    with pytest.raises(LayoutDegradedError, match="omitted 6"):
        sl.planning.plan(
            sl.budget(sl.paragraph("abcdefghij"), min=2, prefer=4),
            target=sl.html.target(),
            strict=True,
        )

    paged = sl.semantic.Paged(
        sl.stack(sl.paragraph("aaaa"), sl.paragraph("bbbb"), sl.paragraph("cccc")),
        key="manual",
        chars=5,
    )
    second = sl.planning.plan(paged, target=sl.html.target(), positions={"manual": Position(offset=1)})
    assert second.scene.pagers[0].page == 1
    assert "bbbb" in [
        node.content for node in testing.walk(second.scene.body.children) if isinstance(node, scene.HtmlText)
    ]
    assert "aaaa" not in [
        node.content for node in testing.walk(second.scene.body.children) if isinstance(node, scene.HtmlText)
    ]


def test_html_searches_authored_fallbacks_only_under_an_explicit_budget() -> None:
    fallback = sl.fallback(sl.paragraph("preferred is too long"), sl.paragraph("short"))

    preferred = sl.planning.plan(fallback, target=sl.html.target())
    constrained = sl.planning.plan(sl.budget(fallback, min=3, prefer=5), target=sl.html.target())

    assert "preferred is too long" in [
        node.content for node in testing.walk(preferred.scene.body.children) if isinstance(node, scene.HtmlText)
    ]
    assert "short" in [
        node.content for node in testing.walk(constrained.scene.body.children) if isinstance(node, scene.HtmlText)
    ]
    assert constrained.metrics.states_explored == 2
    assert any(event.code == "html.budget.fallback" for event in constrained.report.events)
    with pytest.raises(LayoutDegradedError, match="representation 2 of 2"):
        sl.planning.plan(sl.budget(fallback, min=3, prefer=5), target=sl.html.target(), strict=True)


def test_html_plans_assets_and_rebuilds_ephemeral_bindings_on_cache_hits() -> None:
    asset = sl.document.Asset("report", "report.txt", "text/plain", sl.document.InlineAsset(b"report"))
    cache = PlanCache()
    memo = PlanMemo()
    first = sl.planning.plan(
        sl.download("Report", asset, key="report"),
        target=sl.html.target(),
        cache=cache,
        memo=memo,
    )
    exact = sl.planning.plan(
        sl.download("Report", asset, key="report"),
        target=sl.html.target(),
        cache=cache,
    )

    assert first.scene.assets == (scene.Asset("report", "report.txt", "text/plain"),)
    assert first.resources["asset:report"] == asset
    assert exact.metrics.cache_hit


def test_stale_remembered_selections_are_dropped_by_html_planning() -> None:
    # The engine's own stale memory -- a remembered key whose entry or destination no
    # longer exists -- must not survive into the scene; only an author's controlled value
    # may be wrong. Discord already validated these reads; HTML now shares them.
    session = PresentationState()
    session.selections["entries"] = SelectionState(("gone",))
    session.selections["nav"] = SelectionState(("gone",))
    document = sl.group(
        sl.items(sl.item(sl.item_label("One"), sl.paragraph("first"), key="one"), key="entries"),
        sl.navigation(sl.nav_option("Home", key="home"), sl.nav_option("Away", key="away"), key="nav"),
    )

    result = sl.planning.plan(document, target=sl.html.target(), session=session)

    current = [
        attribute.value
        for element in _elements(result)
        if element.tag is scene.HtmlTag.BUTTON
        for attribute in element.attributes
        if attribute.name is scene.HtmlAttributeName.ARIA_CURRENT
    ]
    assert current == ["page"], "navigation falls back to the first available destination"

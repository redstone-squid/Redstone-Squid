"""Every portable node plans through every registered target: no crash, no silent drop.

Static exhaustiveness proves every planner has an arm for every member; this suite is the
runtime backstop it cannot give — an arm that exists but emits nothing still fails here.
A future target joins by adding one entry to `_TARGETS`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from types import UnionType
from typing import Any, TypeAliasType, get_args, get_origin

import pytest

import squid_ui as sl
from squid_ui import scene, semantic
from squid_ui.entity import EntityKind, EntityRef, EntityType
from squid_ui.forms import FormSpec, TextField
from squid_ui.grids import GridCell
from squid_ui.interactions import ActionEvent, SelectionEvent, SubmitEvent
from squid_ui.palette import Palette
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.types import DiscordAdapter
from squid_ui.rosters import RosterEntry, RosterSlot, place_roster
from squid_ui.semantic import PortableNode
from squid_ui.temporal import ZonedDateTime


async def _pressed(_event: ActionEvent) -> None:
    pass


async def _selected(_event: SelectionEvent) -> None:
    pass


async def _submitted(_event: SubmitEvent) -> None:
    pass


_ASSET = sl.document.Asset("report", "report.txt", "text/plain", sl.document.InlineAsset(b"report"))
_FORM = FormSpec("Edit", (TextField("Name", "name"),))
_INSTANT = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)


def _roster() -> semantic.Roster:
    placement = place_roster(
        (RosterEntry("1", "Ada", "builders"),),
        (RosterSlot("builders", "Builders", 2),),
    )
    return sl.roster(placement, key="roster", on_join=_selected)


# One minimal, everywhere-plannable instance per portable member, built through the public
# factories. The drift test below keeps this table exactly aligned with `PortableNode`.
_MINIMAL: dict[type, Callable[[], semantic.AnyLayoutNode]] = {
    semantic.Group: lambda: sl.group(sl.paragraph("grouped")),
    semantic.Stack: lambda: sl.stack(sl.paragraph("stacked")),
    semantic.Cluster: lambda: sl.cluster(sl.paragraph("clustered")),
    semantic.Themed: lambda: sl.themed(Palette(brand=0x5865F2), sl.paragraph("themed")),
    semantic.Block: lambda: sl.block(sl.paragraph("blocked")),
    semantic.Section: lambda: sl.section(sl.heading("Section"), sl.paragraph("body")),
    semantic.Article: lambda: sl.article(sl.heading("Article"), sl.paragraph("body")),
    semantic.Aside: lambda: sl.aside(sl.paragraph("aside")),
    semantic.Details: lambda: sl.details(sl.summary("More"), sl.paragraph("hidden"), key="details"),
    semantic.Items: lambda: sl.items(
        sl.item(sl.item_label("One"), sl.paragraph("first"), key="one"),
        key="entries",
    ),
    semantic.Heading: lambda: sl.heading("Heading"),
    semantic.Paragraph: lambda: sl.paragraph("prose"),
    semantic.Note: lambda: sl.note("aside note"),
    semantic.List: lambda: sl.bullets("first", "second", key="list"),
    semantic.Fields: lambda: sl.fields(sl.field("Name", "Ada")),
    semantic.Table: lambda: sl.table(
        sl.columns(sl.column("Name"), sl.column("Score")),
        sl.table_row("Ada", "10"),
        key="scores",
    ),
    semantic.Quote: lambda: sl.quote("quoted", attribution="Ada"),
    semantic.Code: lambda: sl.code("x = 1", language="python"),
    semantic.Figure: lambda: sl.figure("https://example.invalid/i.png", caption="caption"),
    semantic.Media: lambda: sl.media("https://example.invalid/i.png", key="media"),
    semantic.Toggle: lambda: sl.toggle("Notify", key="toggle"),
    semantic.Download: lambda: sl.download("Report", _ASSET, key="download"),
    semantic.Status: lambda: sl.status("operational"),
    semantic.ProgressBar: lambda: sl.progress(0.5, label="Progress"),
    semantic.Roster: _roster,
    semantic.Grid: lambda: sl.grid(
        GridCell("a", "A"),
        GridCell("b", "B"),
        key="grid",
        columns=2,
        on_pick=_selected,
    ),
    semantic.Metric: lambda: sl.metric(10, "Score"),
    semantic.Timestamp: lambda: sl.timestamp(_INSTANT),
    semantic.ZonedTimestamp: lambda: sl.zoned_timestamp(ZonedDateTime(_INSTANT, "Europe/Berlin")),
    semantic.FormTrigger: lambda: sl.form("Edit", _FORM, key="form", on_submit=_submitted),
    semantic.ActionControls: lambda: sl.action_controls(
        sl.action_control("Go", _pressed, key="go"),
        key="actions",
    ),
    semantic.Choices: lambda: sl.choices(sl.choice("Yes", key="yes"), key="choices"),
    semantic.Entities: lambda: sl.entities(
        sl.entity_choice(EntityRef(EntityKind.USER, 1), "Ada"),
        key="entities",
        entity_type=EntityType.USER,
    ),
    semantic.RoutedChoices: lambda: sl.routed_choices(sl.choice("Yes", key="yes"), key="routed", route_id="r:choices"),
    semantic.Navigation: lambda: sl.navigation(
        sl.nav_option("Home", key="home"),
        sl.nav_option("Away", key="away"),
        key="nav",
    ),
    semantic.Truncated: lambda: sl.truncate(sl.paragraph("truncatable")),
    semantic.Spilled: lambda: sl.spill(sl.paragraph("spillable")),
    semantic.OptionalContent: lambda: sl.optional(sl.paragraph("optional")),
    semantic.BestEffort: lambda: sl.best_effort(sl.paragraph("best effort")),
    semantic.Budgeted: lambda: sl.budget(sl.paragraph("budgeted"), min=0, prefer=200),
    semantic.Unbreakable: lambda: sl.unbreakable(sl.paragraph("atomic")),
    semantic.KeepWithNext: lambda: sl.keep_with_next(sl.paragraph("attached")),
    semantic.Paged: lambda: sl.paged(sl.paragraph("paged"), key="pager", chars=200).node,
    semantic.FallbackContent: lambda: sl.fallback(sl.paragraph("primary"), sl.paragraph("alternate")),
}

_TARGETS: dict[str, Callable[[], Any]] = {
    "html": lambda: sl.html.target(),
    "discord-v2": lambda: components_v2_target(AdapterProfile(DiscordAdapter, "conformance", ">=1")),
    "discord-classic": lambda: classic_target(AdapterProfile(DiscordAdapter, "conformance", ">=1")),
}

# A pair a target refuses documents its refusal here, message and all; a pair with no
# entry must plan, so fixing a gap forces its entry's removal. Every entry below is a
# genuine classic-dialect gap this suite surfaced, in any placement, not only at the root
# -- none is a designed refusal:
# - timestamps: classic could render `<t:...>` markup, but MeasuredTime cannot pass the
#   card-description textifier;
# - budget/break wrappers: the classic converter cannot unwrap them even around plain text;
# - media: the "classic media strategy" the refusal points at is never nominated.
_REFUSALS: dict[tuple[type, str], str] = {
    (semantic.Timestamp, "discord-classic"): "MeasuredTime cannot appear in a card description",
    (semantic.ZonedTimestamp, "discord-classic"): "MeasuredZonedTime cannot appear in a card description",
    (semantic.Media, "discord-classic"): "media galleries require Components V2 or a classic media strategy",
    (semantic.Budgeted, "discord-classic"): "MeasuredText cannot appear in a classic message",
    (semantic.Unbreakable, "discord-classic"): "MeasuredText cannot appear in a classic message",
    (semantic.KeepWithNext, "discord-classic"): "MeasuredText cannot appear in a classic message",
    (semantic.Paged, "discord-classic"): "MeasuredText cannot appear in a classic message",
}


def _members(annotation: object) -> set[type]:
    if isinstance(annotation, TypeAliasType):
        return _members(annotation.__value__)
    if isinstance(annotation, UnionType):
        return {member for arg in get_args(annotation) for member in _members(arg)}
    if isinstance(annotation, type):
        return {annotation}
    origin = get_origin(annotation)
    if isinstance(origin, type):
        return {origin}
    if isinstance(origin, TypeAliasType):
        return _members(origin)
    message = f"unexpected union member {annotation!r}"
    raise AssertionError(message)


def test_the_minimal_table_covers_the_portable_union_exactly() -> None:
    assert _members(PortableNode) == set(_MINIMAL)


def _non_trivial(body: object) -> bool:
    match body:
        case scene.HtmlBody(children=children) | scene.ComponentsV2(children=children):
            return bool(children)
        case scene.ClassicMessage(content=content, embeds=embeds, rows=rows):
            return bool(content or embeds or rows)
        case _:
            message = f"unknown scene body {type(body).__name__}"
            raise AssertionError(message)


@pytest.mark.parametrize("target_name", _TARGETS)
@pytest.mark.parametrize("member", _MINIMAL, ids=lambda member: member.__name__)
def test_every_portable_node_plans_on_every_target(member: type, target_name: str) -> None:
    refusal = _REFUSALS.get((member, target_name))
    build = _MINIMAL[member]
    target = _TARGETS[target_name]()
    if refusal is not None:
        with pytest.raises(Exception, match=refusal):
            sl.planning.plan(build(), target=target)
        return

    result = sl.planning.plan(build(), target=target)

    assert _non_trivial(result.scene.body), f"{member.__name__} planned to an empty {target_name} body"


@pytest.mark.parametrize("member", (semantic.Details, semantic.Toggle, semantic.Navigation), ids=lambda m: m.__name__)
def test_stateful_nodes_bind_the_same_action_keys_on_every_target(member: type) -> None:
    # Rebinding after a replan only finds its handler because every target derives the
    # same action keys from the same document.
    build = _MINIMAL[member]
    bindings = {name: set(sl.planning.plan(build(), target=make()).bindings) for name, make in _TARGETS.items()}

    first, *rest = bindings.values()
    assert all(current == first for current in rest), bindings

"""Native semantic-document planning for the HTML target."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from typing import Any, cast

from squid_ui import forms as form_types
from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.chrome import Chrome
from squid_ui.document import DocumentLike, as_document
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.palette import AccentDefault, Palette
from squid_ui.planning.cache import CachedPlan, PlanCache, PlanMemo
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.semantic_adaptation.handlers import (
    ChoiceCommit,
    EntityCommit,
    FlipToggle,
    FocusItem,
    ForwardSelection,
    GoToDestination,
    ItemCommit,
    NavigationCommit,
    PresentForm,
    SelectChoices,
    SelectEntities,
    ToggleDetails,
)
from squid_ui.planning.target import Target
from squid_ui.planning.text_allocation import allocate_pages, truncate_text
from squid_ui.runtime.presentation_state import (
    ActivePagers,
    CursorState,
    CursorUpdate,
    PresentationState,
    SessionUpdate,
)
from squid_ui.scene.model import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanReuse, PlanSeverity
from squid_ui.sources import POSITION_RESOLVER, Direction, Position
from squid_ui.target_types import HtmlTarget
from squid_ui.text import Localization, TextLike, resolve_text

type HtmlTargetT = Target[Any, scene.HtmlBody, HtmlTarget, Any]


def _attribute(name: scene.HtmlAttributeName, value: scene.HtmlAttributeValue) -> scene.HtmlAttribute:
    return scene.HtmlAttribute(name, value)


def _attributes(
    *values: tuple[scene.HtmlAttributeName, scene.HtmlAttributeValue | None],
) -> tuple[scene.HtmlAttribute, ...]:
    return tuple(_attribute(name, value) for name, value in values if value is not None)


def _entity_key(ref: object) -> str:
    kind = getattr(getattr(ref, "kind", None), "value", "entity")
    return f"{kind}:{getattr(ref, 'id', '')}"


def _scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, date | datetime | time):
        return value.isoformat()
    return "" if value is None else str(value)


def _html_size(node: scene.HtmlNode) -> int:
    if isinstance(node, scene.HtmlText):
        return len(node.content)
    return sum(_html_size(child) for child in node.children)


def _truncate_nodes(
    nodes: Sequence[scene.HtmlNode], capacity: int, *, keep: str = "head"
) -> tuple[tuple[scene.HtmlNode, ...], int]:
    remaining = capacity
    omitted = 0

    def visit(node: scene.HtmlNode) -> scene.HtmlNode | None:
        nonlocal remaining, omitted
        if isinstance(node, scene.HtmlText):
            fitted, lost = truncate_text(node.content, remaining, keep=keep)
            remaining -= len(fitted)
            omitted += lost
            return replace(node, content=fitted) if fitted else None
        children = [fitted for child in node.children if (fitted := visit(child)) is not None]
        return replace(node, children=tuple(children))

    fitted_nodes = tuple(fitted for node in nodes if (fitted := visit(node)) is not None)
    return fitted_nodes, omitted


def _action_keys(nodes: Sequence[scene.HtmlNode]) -> tuple[set[str], set[str]]:
    actions: set[str] = set()
    forms: set[str] = set()

    def visit(node: scene.HtmlNode) -> None:
        if isinstance(node, scene.HtmlText):
            return
        if node.action is not None:
            actions.add(node.action.action)
        if node.form is not None:
            forms.add(node.form.key)
        for child in node.children:
            visit(child)

    for node in nodes:
        visit(node)
    return actions, forms


@dataclass(slots=True)
class _Compiler:
    target: HtmlTargetT
    chrome: Chrome
    localization: Localization
    palette: Palette
    presentation: PresentationState
    positions: Mapping[str, Position] | None
    strict: bool
    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    form_bindings: dict[str, FormBinding] = field(default_factory=dict)
    assets: dict[str, Asset] = field(default_factory=dict)
    events: list[PlanEvent] = field(default_factory=list)
    pagers: list[scene.Pager] = field(default_factory=list)
    updates: list[SessionUpdate] = field(default_factory=list)
    states_explored: int = 1

    def text(self, value: TextLike) -> scene.HtmlText:
        resolved = resolve_text(value, self.localization)
        return scene.HtmlText(resolved.content, resolved.markup)

    def element(
        self,
        tag: scene.HtmlTag,
        *children: scene.HtmlNode,
        attributes: tuple[scene.HtmlAttribute, ...] = (),
        action: scene.HtmlActionRef | None = None,
        route: scene.HtmlRouteRef | None = None,
        form: scene.HtmlFormRef | None = None,
        url: scene.HtmlUrlRef | None = None,
        time_ref: scene.HtmlTimeRef | None = None,
        colour: int | None = None,
        asset: scene.HtmlAssetRef | None = None,
    ) -> scene.HtmlElement:
        return scene.HtmlElement(
            tag,
            tuple(children),
            attributes,
            action,
            route,
            form,
            url,
            time_ref,
            None if colour is None else scene.HtmlColourRef(colour),
            asset,
        )

    def bind(
        self,
        key: str,
        handler: object,
        *,
        mode: ActionMode = ActionMode.EXCLUSIVE,
        routes: Mapping[str, ActionBinding] | None = None,
        guard: object | None = None,
        busy: object | None = None,
        label: TextLike = "",
        record: object | None = None,
    ) -> scene.HtmlActionRef:
        if key in self.bindings:
            message = f"duplicate action key {key!r}"
            raise LayoutInvariantError(message)
        self.bindings[key] = ActionBinding(
            key,
            cast(Any, handler),
            mode,
            {} if routes is None else routes,
            cast(Any, guard),
            cast(Any, busy),
            label,
            cast(Any, record),
        )
        return scene.HtmlActionRef(key, mode)

    def compile_children(self, children: Sequence[sem.AnyLayoutNode], path: str) -> tuple[scene.HtmlNode, ...]:
        return tuple(
            compiled for index, child in enumerate(children) for compiled in self.compile(child, f"{path}.{index}")
        )

    def compile(self, node: sem.AnyLayoutNode, path: str) -> tuple[scene.HtmlNode, ...]:
        """Resolve one semantic node into the HTML nodes that stand for it.

        The stages partition the semantic union by what compiling a member actually needs:
        a wrapper around compiled children, a self-contained display element, a binding into
        session state, or an adaptation to unwrap. Each returns `None` for a node it does not
        claim — not an empty tuple, which is a legitimate result — so a union member added
        without an arm still reaches the same rejection one long `match` gave it.
        """
        compiled = self._container(node, path)
        if compiled is not None:
            return compiled
        compiled = self._display(node)
        if compiled is not None:
            return compiled
        compiled = self._interactive(node, path)
        if compiled is not None:
            return compiled
        compiled = self._adapted(node, path)
        if compiled is not None:
            return compiled
        message = f"HTML planning accepts semantic nodes, not Discord-shaped primitive {type(node).__name__}"
        raise LayoutInvariantError(message)

    def _container(self, node: sem.AnyLayoutNode, path: str) -> tuple[scene.HtmlNode, ...] | None:
        """Wrap compiled children in the element carrying the node's grouping semantics."""
        match node:
            case sem.Group(children=children):
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        *self.compile_children(children, path),
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-group")),
                    ),
                )
            case sem.Stack(children=children):
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        *self.compile_children(children, path),
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-stack")),
                    ),
                )
            case sem.Cluster(children=children):
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        *self.compile_children(children, path),
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-cluster")),
                    ),
                )
            case sem.Themed(children=children, palette=palette):
                previous = self.palette
                self.palette = palette
                try:
                    compiled = self.compile_children(children, path)
                finally:
                    self.palette = previous
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        *compiled,
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-themed")),
                    ),
                )
            case sem.Block(children=children, accent=accent):
                colour = self.palette.brand if accent is AccentDefault.INHERIT else accent
                return (
                    self.element(
                        scene.HtmlTag.SECTION,
                        *self.compile_children(children, path),
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-block")),
                        colour=colour,
                    ),
                )
            case sem.Section(heading=heading, children=children, accent=accent, thumbnail=thumbnail):
                colour = self.palette.brand if accent is AccentDefault.INHERIT else accent
                content = [self._heading(heading), *self.compile_children(children, path)]
                if thumbnail is not None:
                    content.insert(
                        1,
                        self.element(
                            scene.HtmlTag.IMG,
                            attributes=_attributes((scene.HtmlAttributeName.ALT, self._resolved(heading.content))),
                            url=scene.HtmlUrlRef(thumbnail),
                        ),
                    )
                return (
                    self.element(
                        scene.HtmlTag.SECTION,
                        *content,
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-section")),
                        colour=colour,
                    ),
                )
            case sem.Article(heading=heading, children=children, accent=accent, thumbnail=thumbnail):
                colour = self.palette.brand if accent is AccentDefault.INHERIT else accent
                content = [self._heading(heading), *self.compile_children(children, path)]
                if thumbnail is not None:
                    content.insert(
                        1,
                        self.element(
                            scene.HtmlTag.IMG,
                            attributes=_attributes((scene.HtmlAttributeName.ALT, self._resolved(heading.content))),
                            url=scene.HtmlUrlRef(thumbnail),
                        ),
                    )
                return (
                    self.element(
                        scene.HtmlTag.ARTICLE,
                        *content,
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-article")),
                        colour=colour,
                    ),
                )
            case sem.Aside(children=children, tone=tone):
                return (
                    self.element(
                        scene.HtmlTag.ASIDE,
                        *self.compile_children(children, path),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.CLASS, "squid-aside"),
                            (scene.HtmlAttributeName.TONE, tone.value),
                        ),
                        colour=self.palette.tone(tone),
                    ),
                )
            case _:
                return None

    def _display(self, node: sem.AnyLayoutNode) -> tuple[scene.HtmlNode, ...] | None:
        """Resolve a node that draws itself: no children to compile, no state to bind."""
        match node:
            case sem.Heading():
                return (self._heading(node),)
            case sem.Paragraph(content=content):
                return (self.element(scene.HtmlTag.P, self.text(content)),)
            case sem.Note(content=content):
                return (self.element(scene.HtmlTag.SMALL, self.text(content)),)
            case sem.List(items=items, ordered=ordered):
                return (
                    self.element(
                        scene.HtmlTag.OL if ordered else scene.HtmlTag.UL,
                        *(self.element(scene.HtmlTag.LI, self.text(item.content)) for item in items),
                    ),
                )
            case sem.Fields(fields=fields):
                values = tuple(
                    part
                    for item in fields
                    for part in (
                        self.element(scene.HtmlTag.DT, self.text(item.label)),
                        self.element(scene.HtmlTag.DD, self.text(item.value)),
                    )
                )
                return (
                    self.element(
                        scene.HtmlTag.DL,
                        *values,
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-fields")),
                    ),
                )
            case sem.Table(columns=columns, rows=rows):
                heading = self.element(
                    scene.HtmlTag.THEAD,
                    self.element(
                        scene.HtmlTag.TR,
                        *(
                            self.element(
                                scene.HtmlTag.TH,
                                self.text(column.heading),
                                attributes=_attributes((scene.HtmlAttributeName.SCOPE, "col")),
                            )
                            for column in columns.columns
                        ),
                    ),
                )
                body = self.element(
                    scene.HtmlTag.TBODY,
                    *(
                        self.element(
                            scene.HtmlTag.TR,
                            *(self.element(scene.HtmlTag.TD, self.text(cell)) for cell in row.cells),
                        )
                        for row in rows
                    ),
                )
                return (self.element(scene.HtmlTag.TABLE, heading, body),)
            case sem.Quote(content=content, attribution=attribution):
                children: list[scene.HtmlNode] = [self.element(scene.HtmlTag.P, self.text(content))]
                if attribution is not None:
                    children.append(self.element(scene.HtmlTag.FOOTER, self.text(attribution)))
                return (self.element(scene.HtmlTag.BLOCKQUOTE, *children),)
            case sem.Code(content=content, language=language):
                code_attrs = _attributes((scene.HtmlAttributeName.CLASS, f"language-{language}" if language else None))
                return (
                    self.element(
                        scene.HtmlTag.PRE,
                        self.element(scene.HtmlTag.CODE, scene.HtmlText(content), attributes=code_attrs),
                    ),
                )
            case sem.Figure(media=media, caption=caption):
                children = [self._image(media)]
                if caption is not None:
                    children.append(self.element(scene.HtmlTag.FIGCAPTION, self.text(caption)))
                return (self.element(scene.HtmlTag.FIGURE, *children),)
            case sem.Media(items=items, display=display):
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        *(self.element(scene.HtmlTag.FIGURE, self._image(item)) for item in items),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.CLASS, "squid-gallery"),
                            (scene.HtmlAttributeName.DISPLAY, display.value),
                        ),
                    ),
                )
            case sem.Status(content=content, tone=tone, emphasis=emphasis):
                return (
                    self.element(
                        scene.HtmlTag.DIV,
                        self.text(content),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.ROLE, "status"),
                            (scene.HtmlAttributeName.TONE, tone.value),
                            (scene.HtmlAttributeName.EMPHASIS, emphasis.value),
                        ),
                        colour=self.palette.tone(tone),
                    ),
                )
            case sem.ProgressBar(value=value, label=label, maximum=maximum):
                progress = self.element(
                    scene.HtmlTag.PROGRESS,
                    scene.HtmlText(f"{value}/{maximum}"),
                    attributes=_attributes(
                        (scene.HtmlAttributeName.VALUE, value),
                        (scene.HtmlAttributeName.MAX, maximum),
                        (scene.HtmlAttributeName.ARIA_LABEL, None if label is None else self._resolved(label)),
                    ),
                )
                return (progress,)
            case sem.Roster():
                return (self._roster(node),)
            case sem.Grid():
                return (self._grid(node),)
            case sem.Metric(value=value, label=label, unit=unit):
                rendered = f"{value}{'' if unit is None else f' {unit}'}"
                return (
                    self.element(
                        scene.HtmlTag.DL,
                        self.element(scene.HtmlTag.DT, self.text(label)),
                        self.element(scene.HtmlTag.DD, scene.HtmlText(rendered)),
                        attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-metric")),
                    ),
                )
            case sem.Timestamp(instant=instant, style=style, label=label):
                time_node = self.element(
                    scene.HtmlTag.TIME,
                    scene.HtmlText(instant.isoformat()),
                    time_ref=scene.HtmlTimeRef(instant.isoformat(), style=style.value),
                )
                return self._labelled_time(label, time_node)
            case sem.ZonedTimestamp(value=value, label=label):
                time_node = self.element(
                    scene.HtmlTag.TIME,
                    scene.HtmlText(value.isoformat()),
                    time_ref=scene.HtmlTimeRef(value.instant.isoformat(), timezone=value.timezone),
                )
                return self._labelled_time(label, time_node)
            case _:
                return None

    def _interactive(self, node: sem.AnyLayoutNode, path: str) -> tuple[scene.HtmlNode, ...] | None:
        """Resolve a node whose HTML depends on session state, a binding, or a route."""
        match node:
            case sem.Details(key=key, summary=summary, children=children):
                open_ = self._disclosure(node)
                action = self.bind(f"{key}.toggle", ToggleDetails(node, open_, self.presentation))
                return (
                    self.element(
                        scene.HtmlTag.DETAILS,
                        self.element(scene.HtmlTag.SUMMARY, self.text(summary.content)),
                        *self.compile_children(children, path),
                        attributes=_attributes((scene.HtmlAttributeName.OPEN, open_ if open_ else None)),
                        action=action,
                    ),
                )
            case sem.Toggle(key=key, label=label, available=available):
                on = self._toggle_state(node)
                action = self.bind(key, FlipToggle(node, on, self.presentation), label=label)
                checkbox = self.element(
                    scene.HtmlTag.INPUT,
                    attributes=_attributes(
                        (scene.HtmlAttributeName.TYPE, "checkbox"),
                        (scene.HtmlAttributeName.NAME, key),
                        (scene.HtmlAttributeName.CHECKED, on if on else None),
                        (scene.HtmlAttributeName.DISABLED, True if not available else None),
                    ),
                    action=action,
                    form=scene.HtmlFormRef(key, key),
                )
                return (self.element(scene.HtmlTag.LABEL, checkbox, self.text(label)),)
            case sem.Download(key=key, label=label, asset=asset, description=description, spoiler=spoiler):
                self._asset(asset)
                title = self.text(self.chrome.download if label is None else label)
                link = self.element(
                    scene.HtmlTag.A,
                    title,
                    attributes=_attributes(
                        (
                            scene.HtmlAttributeName.CLASS,
                            "squid-download squid-spoiler" if spoiler else "squid-download",
                        ),
                        (scene.HtmlAttributeName.DOWNLOAD, asset.name),
                    ),
                    asset=scene.HtmlAssetRef(asset.key, asset.name, asset.media_type),
                )
                if description is None:
                    return (link,)
                return (self.element(scene.HtmlTag.DIV, link, self.element(scene.HtmlTag.P, self.text(description))),)
            case sem.FormTrigger():
                return (self._form(node),)
            case sem.ActionControls(items=items, display=display):
                return (
                    self.element(
                        scene.HtmlTag.NAV,
                        *(self._action_item(item) for item in items),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.CLASS, "squid-actions"),
                            (scene.HtmlAttributeName.DISPLAY, display.value),
                            (scene.HtmlAttributeName.ARIA_LABEL, "Actions"),
                        ),
                    ),
                )
            case sem.Choices():
                return (self._choices(node),)
            case sem.Entities():
                return (self._entities(node),)
            case sem.RoutedChoices():
                return (self._routed_choices(node),)
            case sem.Items():
                return (self._items(node, path),)
            case sem.Navigation():
                return (self._navigation(node),)
            case _:
                return None

    def _adapted(self, node: sem.AnyLayoutNode, path: str) -> tuple[scene.HtmlNode, ...] | None:
        """Unwrap a planner adaptation.

        HTML has no message budget to overflow, so most of these compile straight through to
        the node they wrap; only paging and an explicit budget change what is emitted.
        """
        match node:
            case sem.Truncated(node=child) | sem.Spilled(node=child) | sem.BestEffort(node=child):
                return self.compile(child, path)
            case sem.OptionalContent(node=child):
                return self.compile(child, path)
            case sem.FallbackContent(primary=primary):
                return self.compile(primary, f"{path}.primary")
            case sem.Budgeted(node=child, preferred=preferred, stretch=stretch):
                if isinstance(child, sem.Paged):
                    return self._paged(child, path)
                if isinstance(child, sem.FallbackContent):
                    return self._budgeted_fallback(child, preferred + stretch, path)
                compiled = self.compile(child, path)
                return self._fit_budget(compiled, preferred + stretch, path)
            case sem.Paged():
                return self._paged(node, path)
            case sem.Unbreakable(node=child) | sem.KeepWithNext(node=child):
                return self.compile(child, path)
            case _:
                return None

    def _resolved(self, value: TextLike) -> str:
        return resolve_text(value, self.localization).content

    def _fork(self) -> _Compiler:
        return replace(
            self,
            bindings=dict(self.bindings),
            form_bindings=dict(self.form_bindings),
            assets=dict(self.assets),
            events=list(self.events),
            pagers=list(self.pagers),
            updates=list(self.updates),
        )

    def _adopt(self, candidate: _Compiler) -> None:
        self.bindings = candidate.bindings
        self.form_bindings = candidate.form_bindings
        self.assets = candidate.assets
        self.events = candidate.events
        self.pagers = candidate.pagers
        self.updates = candidate.updates

    def _fit_budget(self, compiled: tuple[scene.HtmlNode, ...], capacity: int, path: str) -> tuple[scene.HtmlNode, ...]:
        fitted, omitted = _truncate_nodes(compiled, capacity)
        if omitted:
            self.events.append(
                PlanEvent(
                    "html.budget.truncated",
                    path,
                    f"Authored HTML budget omitted {omitted} characters",
                    PlanSeverity.DEGRADATION,
                    before={"characters": capacity + omitted},
                    after={"characters": capacity},
                )
            )
        return fitted

    def _budgeted_fallback(
        self, fallback: sem.FallbackContent[Any], capacity: int, path: str
    ) -> tuple[scene.HtmlNode, ...]:
        branches = (fallback.primary, *fallback.alternates)
        for index, branch in enumerate(branches):
            candidate = self._fork()
            compiled = candidate.compile(branch, f"{path}.fallback.{index}")
            if index:
                self.states_explored += 1
            if sum(_html_size(node) for node in compiled) <= capacity:
                self._adopt(candidate)
                if index:
                    self.events.append(
                        PlanEvent(
                            "html.budget.fallback",
                            path,
                            f"Authored HTML budget selected representation {index + 1} of {len(branches)}",
                            PlanSeverity.DEGRADATION,
                            before={"representation": 1},
                            after={"representation": index + 1},
                        )
                    )
                return compiled
        compiled = self.compile(fallback.primary, f"{path}.fallback.0")
        return self._fit_budget(compiled, capacity, path)

    def _heading(self, heading: sem.Heading) -> scene.HtmlElement:
        level = min(6, max(1, heading.level))
        tag = getattr(scene.HtmlTag, f"H{level}")
        return self.element(tag, self.text(heading.content))

    def _image(self, item: sem.MediaItem) -> scene.HtmlElement:
        return self.element(
            scene.HtmlTag.IMG,
            attributes=_attributes(
                (scene.HtmlAttributeName.ALT, "" if item.description is None else self._resolved(item.description)),
                (scene.HtmlAttributeName.CLASS, "squid-spoiler" if item.spoiler else None),
            ),
            url=scene.HtmlUrlRef(item.url),
        )

    def _asset(self, asset: Asset) -> None:
        existing = self.assets.get(asset.key)
        if existing is not None and existing != asset:
            message = f"asset key {asset.key!r} identifies two different assets"
            raise LayoutInvariantError(message)
        self.assets.setdefault(asset.key, asset)

    def _disclosure(self, node: sem.Details) -> bool:
        match node.open:
            case sem.Controlled(value=value):
                return value
            case sem.Uncontrolled(initial=initial):
                return self.presentation.disclosure(node.key, initial=initial).open

    def _toggle_state(self, node: sem.Toggle) -> bool:
        match node.on:
            case sem.Controlled(value=value):
                return value
            case sem.Uncontrolled(initial=initial):
                return self.presentation.toggle(node.key, initial=initial).on

    def _labelled_time(self, label: TextLike | None, time_node: scene.HtmlElement) -> tuple[scene.HtmlNode, ...]:
        if label is None:
            return (time_node,)
        return (self.element(scene.HtmlTag.P, self.text(label), scene.HtmlText(": "), time_node),)

    def _roster(self, node: sem.Roster) -> scene.HtmlElement:
        groups: list[scene.HtmlNode] = []
        for group in node.placement.groups:
            members = self.element(
                scene.HtmlTag.UL,
                *(self.element(scene.HtmlTag.LI, self.text(member.display)) for member in group.members),
            )
            children: list[scene.HtmlNode] = [self.element(scene.HtmlTag.H3, self.text(group.slot.label)), members]
            if node.on_join is not None:
                key = f"{node.key}.{group.slot.key}"
                action = self.bind(key, ForwardSelection(node.on_join, group.slot.key), label=group.slot.label)
                children.append(
                    self.element(
                        scene.HtmlTag.BUTTON,
                        scene.HtmlText("Join"),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.TYPE, "button"),
                            (scene.HtmlAttributeName.DISABLED, True if node.locked else None),
                        ),
                        action=action,
                    )
                )
            elif node.routes is not None:
                children.append(
                    self.element(
                        scene.HtmlTag.BUTTON,
                        scene.HtmlText("Join"),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.TYPE, "button"),
                            (scene.HtmlAttributeName.DISABLED, True if node.locked else None),
                        ),
                        route=scene.HtmlRouteRef(node.routes[group.slot.key]),
                    )
                )
            groups.append(self.element(scene.HtmlTag.SECTION, *children))
        if node.show_waitlist and node.placement.waitlist:
            groups.append(
                self.element(
                    scene.HtmlTag.SECTION,
                    self.element(scene.HtmlTag.H3, scene.HtmlText("Waitlist")),
                    self.element(
                        scene.HtmlTag.UL,
                        *(
                            self.element(scene.HtmlTag.LI, self.text(entry.display))
                            for entry in node.placement.waitlist
                        ),
                    ),
                )
            )
        return self.element(
            scene.HtmlTag.DIV,
            *groups,
            attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-roster")),
        )

    def _grid(self, node: sem.Grid) -> scene.HtmlElement:
        cells: list[scene.HtmlNode] = []
        for cell in node.cells:
            key = f"{node.key}.{cell.key}"
            action = self.bind(key, ForwardSelection(node.on_pick, cell.key), label=cell.label)
            cells.append(
                self.element(
                    scene.HtmlTag.BUTTON,
                    self.text(cell.label),
                    attributes=_attributes(
                        (scene.HtmlAttributeName.TYPE, "button"),
                        (scene.HtmlAttributeName.DISABLED, True if not cell.available else None),
                        (scene.HtmlAttributeName.TONE, cell.tone.value),
                    ),
                    action=action,
                )
            )
        return self.element(
            scene.HtmlTag.DIV,
            *cells,
            attributes=_attributes(
                (scene.HtmlAttributeName.CLASS, "squid-grid"),
                (scene.HtmlAttributeName.ROLE, "grid"),
                (scene.HtmlAttributeName.ARIA_LABEL, node.key),
            ),
        )

    def _form(self, node: sem.FormTrigger) -> scene.HtmlElement:
        spec = node.spec.adapt(self.target.capabilities)
        self.form_bindings[node.key] = FormBinding(node.key, spec, node.on_submit, node.mode, node.label, node.record)
        action = self.bind(
            node.key,
            PresentForm(spec, node.key, node.on_submit, node.mode, node.label, node.record),
            mode=node.mode,
            guard=node.guard,
            label=node.label,
            record=node.record,
        )
        children: list[scene.HtmlNode] = [self.element(scene.HtmlTag.H3, self.text(spec.title))]
        for item in spec.items:
            if isinstance(item, form_types.FormText):
                children.append(self.element(scene.HtmlTag.P, self.text(item.content)))
            else:
                children.append(self._form_field(node.key, spec, item))
        children.append(
            self.element(
                scene.HtmlTag.BUTTON,
                self.text(node.label),
                attributes=_attributes((scene.HtmlAttributeName.TYPE, "submit")),
                action=action,
                form=scene.HtmlFormRef(node.key),
            )
        )
        return self.element(
            scene.HtmlTag.FORM,
            *children,
            attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-form")),
            action=action,
            form=scene.HtmlFormRef(node.key),
        )

    def _form_field(
        self, form_key: str, spec: form_types.FormSpec, field: form_types.FormField[Any]
    ) -> scene.HtmlElement:
        control_id = f"squid-{form_key}-{field.key}"
        description_id = f"{control_id}-description"
        common = _attributes(
            (scene.HtmlAttributeName.ID, control_id),
            (scene.HtmlAttributeName.NAME, field.key),
            (scene.HtmlAttributeName.REQUIRED, field.required if field.required else None),
            (scene.HtmlAttributeName.ARIA_DESCRIBEDBY, description_id if field.description is not None else None),
        )
        prefill = spec.prefill_for(field)
        if isinstance(field, form_types.TextAreaField):
            control = self.element(
                scene.HtmlTag.TEXTAREA,
                scene.HtmlText(_scalar(prefill)),
                attributes=common
                + _attributes(
                    (scene.HtmlAttributeName.PLACEHOLDER, self._optional_text(field.placeholder)),
                    (scene.HtmlAttributeName.MINLENGTH, field.minimum),
                    (scene.HtmlAttributeName.MAXLENGTH, field.maximum),
                ),
                form=scene.HtmlFormRef(form_key, field.key),
            )
        elif isinstance(field, form_types.ChoiceField | form_types.MultiChoiceField | form_types.ScaleField):
            if isinstance(field, form_types.ScaleField):
                options = tuple((str(point), field.label_for(point)) for point in field.points)
                selected = {_scalar(prefill)}
                multiple = False
            else:
                options = tuple((option.key, option.label) for option in field.options)
                selected = set(prefill if isinstance(prefill, tuple) else (_scalar(prefill),))
                multiple = isinstance(field, form_types.MultiChoiceField)
            control = self.element(
                scene.HtmlTag.SELECT,
                *(
                    self.element(
                        scene.HtmlTag.OPTION,
                        self.text(label),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.VALUE, key),
                            (scene.HtmlAttributeName.SELECTED, True if key in selected else None),
                        ),
                    )
                    for key, label in options
                ),
                attributes=common + _attributes((scene.HtmlAttributeName.MULTIPLE, True if multiple else None)),
                form=scene.HtmlFormRef(form_key, field.key),
            )
        else:
            input_type = "text"
            extra: tuple[scene.HtmlAttribute, ...] = ()
            if isinstance(field, form_types.IntField):
                input_type = "number"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, field.minimum),
                    (scene.HtmlAttributeName.MAX, field.maximum),
                    (scene.HtmlAttributeName.STEP, 1),
                )
            elif isinstance(field, form_types.FloatField):
                input_type = "number"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, field.minimum),
                    (scene.HtmlAttributeName.MAX, field.maximum),
                    (scene.HtmlAttributeName.STEP, "any"),
                )
            elif isinstance(field, form_types.DateField):
                input_type = "date"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, None if field.minimum is None else field.minimum.isoformat()),
                    (scene.HtmlAttributeName.MAX, None if field.maximum is None else field.maximum.isoformat()),
                )
            elif isinstance(field, form_types.TimeField):
                input_type = "time"
                extra = _attributes(
                    (scene.HtmlAttributeName.MIN, None if field.minimum is None else field.minimum.isoformat()),
                    (scene.HtmlAttributeName.MAX, None if field.maximum is None else field.maximum.isoformat()),
                )
            elif isinstance(field, form_types.DateTimeField | form_types.ZonedDateTimeField):
                input_type = "datetime-local"
                if isinstance(field, form_types.ZonedDateTimeField):
                    extra = _attributes((scene.HtmlAttributeName.TIMEZONE, field.timezone))
            elif isinstance(field, form_types.BoolField):
                input_type = "checkbox"
                extra = _attributes((scene.HtmlAttributeName.CHECKED, True if prefill else None))
            placeholder = getattr(field, "placeholder", None)
            control = self.element(
                scene.HtmlTag.INPUT,
                attributes=common
                + _attributes(
                    (scene.HtmlAttributeName.TYPE, input_type),
                    (
                        scene.HtmlAttributeName.VALUE,
                        None if isinstance(field, form_types.BoolField) else _scalar(prefill),
                    ),
                    (scene.HtmlAttributeName.PLACEHOLDER, self._optional_text(placeholder)),
                )
                + extra,
                form=scene.HtmlFormRef(form_key, field.key),
            )
        children: list[scene.HtmlNode] = [
            self.element(
                scene.HtmlTag.LABEL,
                self.text(cast(TextLike, field.label)),
                attributes=_attributes((scene.HtmlAttributeName.FOR, control_id)),
            ),
            control,
        ]
        if field.description is not None:
            children.append(
                self.element(
                    scene.HtmlTag.SMALL,
                    self.text(field.description),
                    attributes=_attributes((scene.HtmlAttributeName.ID, description_id)),
                )
            )
        return self.element(
            scene.HtmlTag.DIV, *children, attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-field"))
        )

    def _optional_text(self, value: TextLike | None) -> str | None:
        return None if value is None else self._resolved(value)

    def _action_item(
        self, item: sem.ActionControl | sem.Link | sem.RoutedActionControl | sem.ControlGroup
    ) -> scene.HtmlElement:
        if isinstance(item, sem.ControlGroup):
            children = tuple(self._action_item(control) for control in item.controls)
            if item.label is None:
                return self.element(scene.HtmlTag.DIV, *children)
            return self.element(
                scene.HtmlTag.FIELDSET,
                self.element(scene.HtmlTag.LEGEND, self.text(item.label)),
                *children,
            )
        if isinstance(item, sem.Link):
            return self.element(
                scene.HtmlTag.A,
                self.text(item.label),
                attributes=_attributes((scene.HtmlAttributeName.REL, "noopener noreferrer")),
                url=scene.HtmlUrlRef(item.url),
            )
        if isinstance(item, sem.RoutedActionControl):
            return self.element(
                scene.HtmlTag.BUTTON,
                self.text(item.label),
                attributes=_attributes(
                    (scene.HtmlAttributeName.TYPE, "button"),
                    (scene.HtmlAttributeName.DISABLED, True if not item.available else None),
                    (scene.HtmlAttributeName.TONE, item.tone.value),
                    (scene.HtmlAttributeName.EMPHASIS, item.emphasis.value),
                ),
                route=scene.HtmlRouteRef(item.route_id),
            )
        action = self.bind(
            item.key,
            item.on_trigger,
            mode=item.mode,
            guard=item.guard,
            busy=item.busy,
            label=item.label,
            record=item.record,
        )
        return self.element(
            scene.HtmlTag.BUTTON,
            self.text(item.label),
            attributes=_attributes(
                (scene.HtmlAttributeName.TYPE, "button"),
                (scene.HtmlAttributeName.DISABLED, True if not item.available else None),
                (scene.HtmlAttributeName.TONE, item.tone.value),
                (scene.HtmlAttributeName.EMPHASIS, item.emphasis.value),
            ),
            action=action,
        )

    def _choice_state(self, node: sem.Choices) -> tuple[str, ...]:
        match node.selection:
            case sem.Controlled(value=value):
                return tuple(value)
            case sem.Uncontrolled(initial=initial):
                return self.presentation.selection(node.key, initial=tuple(initial)).selected

    def _choices(self, node: sem.Choices) -> scene.HtmlElement:
        previous = self._choice_state(node)
        commit = ChoiceCommit(node.selection, node.key, previous, self.presentation)
        action = self.bind(node.key, SelectChoices(commit))
        options = tuple(
            self.element(
                scene.HtmlTag.OPTION,
                self.text(choice.label),
                attributes=_attributes(
                    (scene.HtmlAttributeName.VALUE, choice.key),
                    (scene.HtmlAttributeName.SELECTED, True if choice.key in previous else None),
                    (scene.HtmlAttributeName.DISABLED, True if not choice.available else None),
                    (scene.HtmlAttributeName.TITLE, self._optional_text(choice.description)),
                ),
            )
            for choice in node.choices
        )
        return self.element(
            scene.HtmlTag.SELECT,
            *options,
            attributes=_attributes(
                (scene.HtmlAttributeName.NAME, node.key),
                (scene.HtmlAttributeName.MULTIPLE, True if node.maximum > 1 else None),
                (scene.HtmlAttributeName.SELECTION_MIN, node.minimum),
                (scene.HtmlAttributeName.SELECTION_MAX, node.maximum),
                (scene.HtmlAttributeName.ARIA_LABEL, node.key),
            ),
            action=action,
            form=scene.HtmlFormRef(node.key, node.key),
        )

    def _entity_state(self, node: sem.Entities) -> tuple[object, ...]:
        match node.selection:
            case sem.Controlled(value=value):
                return tuple(value)
            case sem.Uncontrolled(initial=initial):
                by_key = {_entity_key(choice.ref): choice.ref for choice in node.choices}
                selected = self.presentation.selection(
                    node.key, initial=tuple(_entity_key(value) for value in initial)
                ).selected
                return tuple(by_key[key] for key in selected if key in by_key)

    def _entities(self, node: sem.Entities) -> scene.HtmlElement:
        previous = self._entity_state(node)
        commit = EntityCommit(cast(Any, node.selection), node.key, cast(Any, previous), self.presentation)
        action = self.bind(node.key, SelectEntities(commit))
        metadata = _attributes(
            (scene.HtmlAttributeName.NAME, node.key),
            (scene.HtmlAttributeName.ENTITY_TYPE, node.entity_type.value),
            (scene.HtmlAttributeName.CHANNEL_TYPES, ",".join(value.value for value in node.channel_types)),
            (scene.HtmlAttributeName.SELECTION_MIN, node.minimum),
            (scene.HtmlAttributeName.SELECTION_MAX, node.maximum),
            (scene.HtmlAttributeName.ARIA_LABEL, self._optional_text(node.placeholder) or node.key),
        )
        if node.choices:
            selected = {_entity_key(value) for value in previous}
            return self.element(
                scene.HtmlTag.SELECT,
                *(
                    self.element(
                        scene.HtmlTag.OPTION,
                        self.text(choice.label),
                        attributes=_attributes(
                            (scene.HtmlAttributeName.VALUE, _entity_key(choice.ref)),
                            (scene.HtmlAttributeName.SELECTED, True if _entity_key(choice.ref) in selected else None),
                            (scene.HtmlAttributeName.DISABLED, True if not choice.available else None),
                            (scene.HtmlAttributeName.TITLE, self._optional_text(choice.description)),
                        ),
                    )
                    for choice in node.choices
                ),
                attributes=metadata
                + _attributes((scene.HtmlAttributeName.MULTIPLE, True if node.maximum > 1 else None)),
                action=action,
                form=scene.HtmlFormRef(node.key, node.key),
            )
        return self.element(
            scene.HtmlTag.INPUT,
            attributes=metadata
            + _attributes(
                (scene.HtmlAttributeName.TYPE, "text"),
                (scene.HtmlAttributeName.VALUE, ",".join(_entity_key(value) for value in previous)),
                (scene.HtmlAttributeName.PLACEHOLDER, self._optional_text(node.placeholder)),
            ),
            action=action,
            form=scene.HtmlFormRef(node.key, node.key),
        )

    def _routed_choices(self, node: sem.RoutedChoices) -> scene.HtmlElement:
        return self.element(
            scene.HtmlTag.SELECT,
            *(
                self.element(
                    scene.HtmlTag.OPTION,
                    self.text(choice.label),
                    attributes=_attributes(
                        (scene.HtmlAttributeName.VALUE, choice.key),
                        (scene.HtmlAttributeName.DISABLED, True if not choice.available else None),
                        (scene.HtmlAttributeName.TITLE, self._optional_text(choice.description)),
                    ),
                )
                for choice in node.choices
            ),
            attributes=_attributes(
                (scene.HtmlAttributeName.NAME, node.key),
                (scene.HtmlAttributeName.MULTIPLE, True if node.maximum > 1 else None),
                (scene.HtmlAttributeName.DISABLED, True if not node.available else None),
                (scene.HtmlAttributeName.SELECTION_MIN, node.minimum),
                (scene.HtmlAttributeName.SELECTION_MAX, node.maximum),
                (scene.HtmlAttributeName.ARIA_LABEL, self._optional_text(node.placeholder) or node.key),
            ),
            route=scene.HtmlRouteRef(node.route_id),
        )

    def _items(self, node: sem.Items, path: str) -> scene.HtmlElement:
        match node.opened:
            case sem.Controlled(value=value):
                opened = value
            case sem.Uncontrolled(initial=initial):
                selected = self.presentation.selection(node.key, initial=() if initial is None else (initial,)).selected
                opened = selected[0] if selected else None
        commit = ItemCommit(node.opened, node.key, self.presentation)
        rendered: list[scene.HtmlNode] = []
        for index, item in enumerate(node.items):
            key = f"{node.key}.{item.key}"
            action = self.bind(key, FocusItem(commit), label=item.label.content)
            children = [
                self.element(scene.HtmlTag.SUMMARY, self.text(item.label.content)),
                *self.compile_children(item.children, f"{path}.{index}"),
            ]
            rendered.append(
                self.element(
                    scene.HtmlTag.DETAILS,
                    *children,
                    attributes=_attributes(
                        (scene.HtmlAttributeName.OPEN, True if item.key == opened else None),
                        (scene.HtmlAttributeName.TITLE, self._optional_text(item.summary)),
                    ),
                    action=action,
                )
            )
        return self.element(
            scene.HtmlTag.DIV,
            *rendered,
            attributes=_attributes(
                (scene.HtmlAttributeName.CLASS, "squid-items"),
                (scene.HtmlAttributeName.DISPLAY, node.display.value),
            ),
        )

    def _navigation(self, node: sem.Navigation) -> scene.HtmlElement:
        available = tuple(option for option in node.options if option.available)
        match node.current:
            case sem.Controlled(value=value):
                current = value
            case sem.Uncontrolled(initial=initial):
                remembered = self.presentation.selection(
                    node.key, initial=() if initial is None else (initial,)
                ).selected
                current = remembered[0] if remembered else None
        if current is None and available:
            current = available[0].key
        commit = NavigationCommit(node.current, node.key, self.presentation)
        return self.element(
            scene.HtmlTag.NAV,
            *(
                self.element(
                    scene.HtmlTag.BUTTON,
                    self.text(option.label),
                    attributes=_attributes(
                        (scene.HtmlAttributeName.TYPE, "button"),
                        (scene.HtmlAttributeName.ARIA_CURRENT, "page" if option.key == current else None),
                    ),
                    action=self.bind(
                        f"{node.key}.{option.key}",
                        GoToDestination(commit, option.key),
                        label=option.label,
                    ),
                )
                for option in available
            ),
            attributes=_attributes(
                (scene.HtmlAttributeName.ARIA_LABEL, node.key),
                (scene.HtmlAttributeName.DISPLAY, node.display.value),
            ),
        )

    def _paged(self, node: sem.Paged, path: str) -> tuple[scene.HtmlNode, ...]:
        children = getattr(node.node, "children", (node.node,))
        compiled = tuple(self.compile(child, f"{path}.page.{index}") for index, child in enumerate(children))
        pages = allocate_pages(
            compiled,
            size=lambda group: sum(_html_size(item) for item in group),
            capacity=node.chars,
            widows=node.widows,
        )
        fingerprint = stable_fingerprint((compiled,))
        extent = len(pages)
        cursor = self.presentation.cursor(node.key)
        position = POSITION_RESOLVER.resolve(
            override=None if self.positions is None else self.positions.get(node.key),
            stale=bool(cursor.fingerprint and cursor.fingerprint != fingerprint),
            stored=cursor.position if node.key in self.presentation.cursors else None,
            initial=Position(offset=extent - 1 if node.initial == "end" else 0),
            upper_bound=extent - 1,
        )
        if extent > 1:
            resolved = Position(offset=position.offset, direction=Direction.AROUND)
            self.pagers.append(scene.Pager(node.key, position.offset, extent, fingerprint))
            self.updates.append(CursorUpdate(node.key, CursorState(resolved, extent, fingerprint)))
        selected = tuple(item for group in pages[position.offset] for item in group)
        if node.footer is not None:
            selected += (self.element(scene.HtmlTag.SMALL, self.text(node.footer(position.offset + 1, extent))),)
        return (
            self.element(
                scene.HtmlTag.DIV,
                *selected,
                attributes=_attributes((scene.HtmlAttributeName.CLASS, "squid-page")),
            ),
        )


class HtmlPlanner:
    """Compile semantic documents directly into safe HTML scene data."""

    def plan(
        self,
        rendered: DocumentLike[HtmlTarget],
        request: PlanRequest[scene.HtmlBody, HtmlTarget, Any],
        *,
        cache: PlanCache | None = None,
        memo: PlanMemo | None = None,
    ) -> PlanResult[scene.HtmlBody]:
        # HTML has neither a bounded document nor a global resource budget, so the two request
        # fields that exist to bound one are refused rather than quietly ignored.
        if request.reservation.values:
            message = "HTML has no reservable global resource axes"
            raise LayoutInvariantError(message)
        target = cast(HtmlTargetT, request.target)
        localization = request.localization
        palette = request.palette
        positions = request.positions
        strict = request.strict
        presentation = request.presentation
        key = stable_fingerprint(
            (
                rendered,
                target.fingerprint,
                localization.locale,
                palette,
                presentation,
                positions,
                strict,
            )
        )
        if memo is not None:
            exact = memo.get(rendered, key, presentation, presentation.revision)
            if isinstance(exact, PlanResult):
                return cast(
                    PlanResult[scene.HtmlBody],
                    replace(exact, metrics=replace(exact.metrics, cache_hit=True, reuse=PlanReuse.EXACT)),
                )
        document = as_document(rendered)
        compiler = _Compiler(target, request.chrome, localization, palette, presentation, positions, strict)
        for asset in document.assets:
            compiler._asset(asset)
        children = compiler.compile_children(document.children, "$")
        if strict and any(event.severity is PlanSeverity.DEGRADATION for event in compiler.events):
            raise LayoutDegradedError("; ".join(event.message for event in compiler.events))
        compiler.updates.append(ActivePagers(frozenset(pager.key for pager in compiler.pagers)))
        planned = scene.Scene(
            protocol=scene.Codec.protocol,
            target=target.id,
            target_version=target.version,
            body=scene.HtmlBody(children, locale=localization.locale),
            assets=tuple(scene.Asset(asset.key, asset.name, asset.media_type) for asset in compiler.assets.values()),
            pagers=tuple(compiler.pagers),
        )
        logical = stable_fingerprint((document, target.fingerprint, localization.locale, palette, positions))
        report = PlanReport(
            tuple(compiler.events),
            logical_fingerprint=logical,
            scene_fingerprint=scene.Codec.fingerprint(planned),
        )
        cached = cache.get(key) if cache is not None else None
        if cached is not None:
            planned = cast(scene.Scene[scene.HtmlBody], cached.scene)
            report = cached.report
        actions, forms = _action_keys(planned.body.children)
        result = PlanResult(
            scene=planned,
            bindings={key: binding for key, binding in compiler.bindings.items() if key in actions},
            form_bindings={key: binding for key, binding in compiler.form_bindings.items() if key in forms},
            report=report,
            resources={f"asset:{asset.key}": asset for asset in compiler.assets.values()},
            metrics=PlanMetrics(
                states_explored=compiler.states_explored,
                cache_hit=cached is not None,
                reuse=PlanReuse.EXACT if cached else PlanReuse.MISS,
            ),
            session_updates=tuple(compiler.updates),
        )
        if cache is not None and cached is None:
            cache.put(
                key,
                CachedPlan(planned, report, tuple(compiler.updates), states_explored=compiler.states_explored),
            )
        if memo is not None:
            memo.put(rendered, key, presentation, presentation.revision, result)
        return result


HTML_PLANNER = HtmlPlanner()


__all__ = ["HTML_PLANNER", "HtmlPlanner"]

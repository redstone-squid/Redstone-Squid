"""Shared lossless realizations for the first composition vocabulary.

Tables retain keyed columns and cells until a target draws the selected form.
This module offers representations; it does not fit or paginate them.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from itertools import pairwise
from typing import Literal
from urllib.parse import quote

from squid_i18n import tree
from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.emoji import Emoji, normalize_emoji
from squid_ui.entity import EntityRef
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.grids import _coordinate
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.ownership import Controlled
from squid_ui.planning.composition import (
    CandidateComposition,
    DiscoveryCompletion,
    FragmentContract,
    FragmentCoverage,
    NavigationInput,
    OccurrenceId,
    OfferDiscovery,
    Placement,
    PlanningContext,
    RealizationOffer,
    RegionContract,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.interaction_semantics import DisclosureToggle, ItemBack, ItemSummary, NavigationChoice
from squid_ui.planning.navigation import PagerNav, default_nav
from squid_ui.planning.pagination_content import RootRegion
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantic_adaptation.common import _button_style
from squid_ui.planning.semantic_adaptation.handlers import (
    ChoiceCommit,
    CloseItem,
    EntityCommit,
    FlipToggle,
    FocusItem,
    GoToDestination,
    ItemCommit,
    NavigationCommit,
    PresentForm,
    RouteSelection,
    SelectChoices,
    SelectDestination,
    SelectEntities,
    SelectEntityFallback,
    ToggleDetails,
)
from squid_ui.planning.semantics import (
    ItemBranch,
    SourceScope,
    StructuralGroup,
    UnsupportedSemanticError,
    check_alternative,
)
from squid_ui.planning.structure import disclosure_state, navigation_current, opened_item, resolve_accent, toggle_state
from squid_ui.planning.text_presentations import (
    ResolvedText,
    UnsupportedText,
    _slice,
    _unsupported,
    join_text,
    resolve_presentation_text,
    text_ends,
    text_fragments,
)
from squid_ui.presentations import (
    Alternatives,
    Budget,
    FidelityEffect,
    Omission,
    Replacement,
    SemanticRef,
    Shortening,
    TextPresentation,
)
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import (
    FIRST_PAGE,
    Alt,
    Alts,
    Condense,
    Drop,
    Never,
    PageOwnership,
    Paginate,
    Spill,
    Truncate,
)
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.text import TextLike, resolve_text, timestamp_text


@dataclass(frozen=True, slots=True)
class TextRealization:
    nodes: tuple[tree.Node, ...]
    provenance: ResolvedText | None = None
    role: Literal["paragraph", "heading", "note", "code", "quote"] = "paragraph"
    level: int = 2
    language: str = ""
    shortening: Shortening | None = None
    ellipsis: bool = True

    @property
    def visible_nodes(self) -> tuple[tree.Node, ...]:
        if self.shortening is None:
            return self.nodes
        operation = self.shortening
        length = sum(len(tree.plain(node)) for node in self.nodes)
        marker = (tree.literal("…"),) if self.ellipsis else ()
        if operation.keep == "head":
            return (*_slice(self.nodes, 0, operation.max_chars), *marker)
        return (*marker, *_slice(self.nodes, length - operation.max_chars, length))

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, nodes=_slice(self.nodes, start, end))


@dataclass(frozen=True, slots=True)
class FieldRealization(TextRealization):
    label: tuple[tree.Node, ...] = ()
    value: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class StatusRealization(TextRealization):
    colour: Color | None = None
    tone: str = "neutral"
    emphasis: str = "normal"


@dataclass(frozen=True, slots=True)
class MetricRealization(TextRealization):
    label: tuple[tree.Node, ...] = ()
    value: str = ""


@dataclass(frozen=True, slots=True)
class TimeRealization(TextRealization):
    instant: str = ""
    display: str = ""
    style: str | None = None
    timezone: str | None = None
    prefix: tuple[tree.Node, ...] = ()
    native_prefix: str | None = None
    label: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgressRealization(TextRealization):
    value: float = 0
    maximum: float = 1
    label: str | None = None


@dataclass(frozen=True, slots=True)
class FlowRealization:
    pass


@dataclass(frozen=True, slots=True)
class FieldsRealization(FlowRealization):
    notice: tuple[tree.Node, ...] = ()


@dataclass(frozen=True, slots=True)
class DisclosureRealization(FlowRealization):
    open: bool


@dataclass(frozen=True, slots=True)
class PanelRealization:
    accent: Color | None
    framed: bool = True
    thumbnail: str | None = None
    heading: str = ""
    kind: Literal["block", "section", "article", "aside"] = "block"
    spoiler: bool = False
    tone: str | None = None


@dataclass(frozen=True, slots=True)
class NativeSectionRealization:
    pass


@dataclass(frozen=True, slots=True)
class ThumbnailRealization:
    node: scene.Thumbnail


@dataclass(frozen=True, slots=True)
class SeparatorRealization:
    node: scene.Separator


@dataclass(frozen=True, slots=True)
class ReferencedText:
    reference: SemanticRef
    nodes: tuple[tree.Node, ...]
    prefix: str = ""
    continuation: bool = False
    ordinal: int = 0


@dataclass(frozen=True, slots=True)
class TextSequenceRealization:
    items: tuple[ReferencedText, ...]
    separator: str = "\n"
    prefix: str = ""
    suffix: str = ""
    list_ordered: bool | None = None
    literal: bool = False
    notice: tuple[tree.Node, ...] = ()

    @property
    def display_items(self) -> tuple[ReferencedText, ...]:
        items: list[ReferencedText] = []
        for item in self.items:
            if item.continuation and items:
                items[-1] = replace(items[-1], nodes=join_text(items[-1].nodes, item.nodes))
            else:
                items.append(item)
        return tuple(items)

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, items=self.items[start:end])


@dataclass(frozen=True, slots=True)
class TableRecord:
    reference: SemanticRef
    cells: tuple[ReferencedText, ...]


class TableForm(StrEnum):
    NATIVE = "table"
    RECORDS = "records"


@dataclass(frozen=True, slots=True)
class TableRealization:
    columns: tuple[ReferencedText, ...]
    rows: tuple[TableRecord, ...]
    form: TableForm

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, rows=self.rows[start:end])


@dataclass(frozen=True, slots=True)
class ImageRealization:
    source: str | Asset
    description: str
    spoiler: bool


@dataclass(frozen=True, slots=True)
class GalleryRealization:
    items: tuple[scene.GalleryItem, ...]
    collection: bool = False

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, items=self.items[start:end])


@dataclass(frozen=True, slots=True)
class FileRealization:
    node: scene.File


@dataclass(frozen=True, slots=True)
class DownloadRealization:
    asset: Asset
    label: tuple[tree.Node, ...]
    description: tuple[tree.Node, ...]
    spoiler: bool


@dataclass(frozen=True, slots=True)
class ControlRealization:
    label: str
    action: str | None = None
    route: str | None = None
    url: str | None = None
    style: ActionStyle = ActionStyle.SECONDARY
    disabled: bool = False
    mode: ActionMode = ActionMode.EXCLUSIVE
    emoji: Emoji | None = None
    current: bool | None = None


@dataclass(frozen=True, slots=True)
class PremiumRealization:
    sku_id: int


@dataclass(frozen=True, slots=True)
class RowRealization:
    pass


@dataclass(frozen=True, slots=True)
class ActionsRealization:
    """Ordered buttons that may occupy multiple target action rows."""

    navigation: bool = False
    columns: int | None = None
    grid_key: str | None = None


@dataclass(frozen=True, slots=True)
class SelectRealization:
    node: scene.Select | scene.RoutedSelect
    navigation: bool = False
    summaries: tuple[ReferencedText, ...] | None = None

    def fragment(self, start: int, end: int) -> Realization:
        return replace(
            self,
            node=replace(self.node, options=self.node.options[start:end]),
            summaries=self.summaries[start:end] if self.summaries is not None else None,
        )


@dataclass(frozen=True, slots=True)
class EntityRealization:
    node: scene.EntitySelect
    choices: tuple[scene.Option, ...] = ()


type Realization = (
    TextSequenceRealization
    | ActionsRealization
    | RowRealization
    | SelectRealization
    | EntityRealization
    | TextRealization
    | FlowRealization
    | PanelRealization
    | NativeSectionRealization
    | ThumbnailRealization
    | SeparatorRealization
    | TableRealization
    | ImageRealization
    | GalleryRealization
    | FileRealization
    | DownloadRealization
    | ControlRealization
    | PremiumRealization
)


@dataclass(frozen=True, slots=True)
class RegionNavigation[RenderTargetT, ContextT]:
    occurrence: SemanticOccurrence[RenderTargetT, ContextT]
    disabled_controls: bool
    footer: Callable[[int, int], TextLike] | None = None
    owner: PageOwnership = FIRST_PAGE
    cursor_key: str = ""
    default_controls: bool = False

    def __call__(
        self, page: int, pages: int, context: PlanningContext
    ) -> tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...]:
        if pages == 1:
            return ()
        occurrence = self.occurrence
        if context.mounted:
            label = (self.footer or context.chrome.page_footer)(page + 1, pages)
            placed = replace(
                occurrence,
                identity=OccurrenceId((*occurrence.identity.path, "@navigation", str(page))),
                placement=Placement.NAVIGATION,
                fragment=FragmentCoverage(occurrence.identity, 0, 0, frozenset()),
            )
            footer = CandidateComposition(
                RealizationOffer(
                    replace(placed, identity=OccurrenceId((*placed.identity.path, "footer"))),
                    "footer",
                    TextRealization(resolve_text(label, context.localization).nodes),
                )
            )
            navigation = context.navigation
            if navigation is None and self.default_controls:
                navigation = PagerNav(default_nav, context.presentation, staged=True)
            controls = (
                () if navigation is None else navigation(self.cursor_key, page, pages, context.chrome, self.owner)
            )
            if not self.disabled_controls:
                controls = tuple(
                    replace(node, items=tuple(item for item in node.items if not getattr(item, "disabled", False)))
                    if isinstance(node, primitive.Row)
                    else node
                    for node in controls
                )
            return (
                footer,
                *(
                    navigation_candidate(
                        node, replace(placed, identity=OccurrenceId((*placed.identity.path, str(index)))), context
                    )
                    for index, node in enumerate(controls)
                ),
            )
        route = "composition.page." + sha256("/".join(occurrence.identity.path).encode()).hexdigest()[:24]
        payloads: list[Realization] = []
        previous = "".join(
            tree.plain(node) for node in resolve_text(context.chrome.previous, context.localization).nodes
        )
        following = "".join(tree.plain(node) for node in resolve_text(context.chrome.next, context.localization).nodes)
        if page > 0 or self.disabled_controls:
            payloads.append(ControlRealization(previous, route=f"{route}.{max(0, page - 1)}", disabled=page == 0))
        label = (self.footer or context.chrome.page_footer)(page + 1, pages)
        payloads.append(TextRealization(resolve_text(label, context.localization).nodes))
        if page + 1 < pages or self.disabled_controls:
            payloads.append(
                ControlRealization(following, route=f"{route}.{min(pages - 1, page + 1)}", disabled=page + 1 == pages)
            )
        result: list[CandidateComposition[RenderTargetT, ContextT, Realization]] = []
        for index, payload in enumerate(payloads):
            context.work.consume()
            identity = OccurrenceId((*occurrence.identity.path, "@navigation", str(page), str(index)))
            placed = replace(
                occurrence,
                identity=identity,
                placement=Placement.NAVIGATION,
                fragment=FragmentCoverage(occurrence.identity, 0, 0, frozenset()),
            )
            navigation = None
            if isinstance(payload, ControlRealization) and payload.route is not None:
                navigation = NavigationInput(
                    occurrence.identity, payload.route, int(payload.route.rsplit(".", 1)[1]), not payload.disabled
                )
            result.append(CandidateComposition(RealizationOffer(placed, "navigation", payload, navigation=navigation)))
        return tuple(result)


def navigation_candidate[RenderTargetT, ContextT](
    node: primitive.Node,
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, Realization]:
    """Expose navigation structure and bindings before its resource certification."""
    context.work.consume()

    def label(value: TextLike | None) -> str | None:
        return (
            None if value is None else "".join(tree.plain(n) for n in resolve_text(value, context.localization).nodes)
        )

    binding = None
    form = None
    children: tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...] = ()
    payload: Realization
    match node:
        case primitive.Row(items=items):
            payload = RowRealization()
            children = tuple(
                navigation_candidate(
                    child, replace(occurrence, identity=OccurrenceId((*occurrence.identity.path, str(index)))), context
                )
                for index, child in enumerate(items)
            )
        case primitive.Button():
            payload = ControlRealization(
                label(node.label) or "",
                action=node.key,
                style=node.style,
                disabled=node.disabled,
                mode=node.mode,
                emoji=normalize_emoji(node.emoji),
            )
            binding = ActionBinding(
                node.key,
                node.on_click,
                node.mode,
                guard=node.guard,
                busy=node.busy,
                label=node.label or "",
                record=node.record,
            )
            if isinstance(node, primitive.FormButton):
                form = node.form
        case primitive.RoutedButton():
            payload = ControlRealization(
                label(node.label) or "",
                route=node.route_id,
                style=node.style,
                disabled=node.disabled,
                emoji=normalize_emoji(node.emoji),
            )
        case primitive.PremiumButton():
            payload = PremiumRealization(node.sku_id)
        case primitive.LinkButton():
            payload = ControlRealization(
                label(node.label) or "", url=node.url, disabled=node.disabled, emoji=normalize_emoji(node.emoji)
            )
        case primitive.SelectMenu() | primitive.RoutedSelect():
            options = tuple(
                scene.Option(
                    label(option.label) or "",
                    option.value,
                    label(option.description),
                    option.default,
                    normalize_emoji(option.emoji),
                )
                for option in node.options
            )
            if isinstance(node, primitive.SelectMenu):
                payload = SelectRealization(
                    scene.Select(
                        options,
                        node.key,
                        label(node.placeholder),
                        node.min_values,
                        node.max_values,
                        node.disabled,
                        node.mode,
                    )
                )
                binding = ActionBinding(node.key, node.on_select, node.mode, routes=node.routes)
            else:
                payload = SelectRealization(
                    scene.RoutedSelect(
                        options, node.route_id, label(node.placeholder), node.min_values, node.max_values, node.disabled
                    )
                )
        case primitive.EntitySelect():
            payload = EntityRealization(
                scene.EntitySelect(
                    node.entity_type,
                    node.key,
                    label(node.placeholder),
                    node.default_values,
                    node.conversation_types,
                    node.min_values,
                    node.max_values,
                    node.disabled,
                    node.mode,
                )
            )
            binding = ActionBinding(node.key, node.on_select, node.mode)
        case _:
            msg = f"navigation {type(node).__name__}"
            raise UnsupportedSemanticError(msg)
    return CandidateComposition(
        RealizationOffer(
            occurrence,
            "navigation",
            payload,
            tuple(child.offer.occurrence for child in children),
            binding=binding,
            form=form,
        ),
        children,
    )


def region_cursor_key[RenderTargetT, ContextT](occurrence: SemanticOccurrence[RenderTargetT, ContextT]) -> str:
    """Keep layout-policy wrappers out of public region cursor paths."""
    if occurrence.origin is not None:
        return "$/" + "/".join(quote(key, safe="") for key in occurrence.page_source.keys[1:])
    parts: list[str] = []
    ref: SemanticRef | None = occurrence.reference
    while ref is not None:
        unit = occurrence.graph.units[ref]
        if unit.parent is not None and not isinstance(
            unit.value,
            sem.Budgeted
            | primitive.Budget
            | primitive.Break
            | sem.KeepWithNext
            | sem.Unbreakable
            | sem.Truncated
            | sem.Spilled
            | sem.BestEffort
            | Budget
            | RootRegion,
        ):
            parts.append(quote(ref.keys[-1], safe=""))
        ref = unit.parent
    return "$/" + "/".join(reversed(parts))


def _control_key[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT], context: PlanningContext
) -> str:
    sources = occurrence.sources
    if occurrence.origin is None and isinstance(
        occurrence.graph.units[occurrence.reference].value, sem.Navigation | sem.Items
    ):
        sources = frozenset({occurrence.reference})
    if len(sources) == 1 and occurrence.origin is None:
        source = next(iter(sources))
        key = source.keys[-1]
        source_key = key
        value = occurrence.graph.units[source].value
        parent = occurrence.graph.units[source].parent
        if parent is not None and isinstance(grid := occurrence.graph.units[parent].value, sem.Grid):
            key = f"{grid.key}.{key}"
        if isinstance(value, DisclosureToggle):
            source_key = value.details.key
            key = f"{source_key}.toggle"
        elif isinstance(value, NavigationChoice):
            source_key = value.navigation.key
            key = f"{source_key}.{value.option.key}"
        elif isinstance(value, ItemBack):
            source_key = value.items.key
            key = f"{source_key}.back"
        elif isinstance(value, sem.Items):
            key += ".focus"
        context.work.consume(len(occurrence.graph.units))
        if (
            not key.startswith("@")
            and len(key) <= 64
            and sum(ref.keys[-1] in (source_key, key) for ref in occurrence.graph.units) == 1
        ):
            return key
    encoded = json.dumps(sorted(ref.keys for ref in sources), ensure_ascii=True)
    return "composition." + sha256(encoded.encode()).hexdigest()[:32]


def semantic_offers[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
    *,
    table_forms: tuple[TableForm, ...],
    panels: bool,
    nested_panels: bool = True,
    disabled_controls: bool = True,
    character_budgets: bool = False,
    text_limit: int | None = None,
    native_controls: bool = False,
    panel_text_limit: int | None = None,
    heading_limit: int | None = None,
    native_v2: bool = False,
    grid_columns: int | None = None,
) -> (
    tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
    | OfferDiscovery[RenderTargetT, ContextT, Realization]
):
    context.work.consume()
    value = occurrence.graph.units[occurrence.reference].value
    text_capacity = context.capacities.get(Axis.DISPLAY_TEXT, text_limit)
    keep: Literal["head", "tail"] | None = None
    spill = False
    inside_panel = False
    for ancestor in occurrence.ancestors():
        inside_panel |= isinstance(ancestor, sem.Block | sem.Section | sem.Article | sem.Aside | primitive.Panel)
        if isinstance(ancestor, sem.Truncated):
            keep = ancestor.keep
            spill = False
        elif isinstance(ancestor, sem.Spilled):
            keep, spill = None, True
        elif isinstance(ancestor, sem.BestEffort):
            keep, spill = "head", True
    if inside_panel and panel_text_limit is not None:
        text_capacity = panel_text_limit
    for ancestor in occurrence.ancestors():
        cap = (
            ancestor.chars
            if isinstance(ancestor, sem.Paged)
            else ancestor.preferred + ancestor.stretch
            if isinstance(ancestor, sem.Budgeted | primitive.Budget)
            else ancestor.limits.get(Axis.DISPLAY_TEXT)
            if isinstance(ancestor, Budget)
            else None
        )
        if cap is not None:
            text_capacity = min(text_capacity, cap) if text_capacity is not None else cap

    def text_offers(
        full: RealizationOffer[RenderTargetT, ContextT, Realization],
        payload: TextRealization,
        permission: Literal["head", "tail"] | None = keep,
        priority: int = 0,
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
        | OfferDiscovery[RenderTargetT, ContextT, Realization]
    ):
        if permission is None or text_capacity is None:
            return (full,)
        if feature := _unsupported(payload.nodes):
            return OfferDiscovery((full,), DiscoveryCompletion.UNSUPPORTED, f"shortening inline {feature}")
        length = sum(len(tree.plain(node)) for node in payload.nodes)
        if not length:
            return (full,)
        cap = (
            min(text_capacity, heading_limit)
            if payload.role == "heading" and heading_limit is not None
            else text_capacity
        )
        upper = min(length - 1, max(0, cap - 1))
        context.work.consume(length + max(0, upper + 1))
        cuts = (0, *text_ends(payload.nodes))
        retained_lengths = sorted(
            {retained for end in cuts if (retained := end if permission == "head" else length - end) <= upper},
            reverse=True,
        )
        return (
            replace(full, preference_priority=priority),
            *(
                replace(
                    full,
                    key=f"shorten-{retained}",
                    payload=replace(payload, shortening=operation),
                    preference=length - retained,
                    effects=(*full.effects, *occurrence.scope_effects((operation,))),
                    fragments=None,
                    preference_priority=priority,
                )
                for retained in retained_lengths
                for operation in (Shortening(occurrence.reference, retained, permission),)
            ),
            replace(
                full,
                key="shorten-empty",
                payload=replace(payload, shortening=Shortening(occurrence.reference, 0, permission), ellipsis=False),
                preference=length + 1,
                effects=(*full.effects, *occurrence.scope_effects((Shortening(occurrence.reference, 0, permission),))),
                fragments=None,
                preference_priority=priority,
            ),
        )

    if isinstance(value, primitive.Lines):
        overflow = Spill() if spill else Truncate(keep) if keep is not None else value.overflow
        if not native_controls or not isinstance(overflow, Never | Truncate | Spill | Drop | Condense | Paginate):
            return ()
        items = tuple(
            ReferencedText(ref, resolve_text(item.content, context.localization).nodes)
            for ref in occurrence.graph.children.get(occurrence.reference, ())
            for item in (occurrence.graph.units[ref].value,)
            if isinstance(item, sem.ListItem)
        )
        line_separator = value.join

        def line_payload(lines: tuple[ReferencedText, ...]) -> TextRealization:
            return TextRealization(
                tuple(
                    node
                    for index, item in enumerate(lines)
                    for node in (
                        *((tree.literal(line_separator),) if index else ()),
                        *item.nodes,
                    )
                )
            )

        payload = line_payload(items)
        line_priority = value.priority
        pagination = overflow if isinstance(overflow, Paginate) else None
        if pagination is not None and pagination.key is None:
            return OfferDiscovery((), DiscoveryCompletion.UNSUPPORTED, "unkeyed primitive pagination")

        def line_offer(
            lines: tuple[ReferencedText, ...],
            key: str,
            preference: int = 0,
            effects: tuple[FidelityEffect, ...] = (),
        ) -> RealizationOffer[RenderTargetT, ContextT, Realization]:
            fragments_items: list[ReferencedText] = []
            required: list[int] = []
            for index, item in enumerate(lines):
                length = sum(len(tree.plain(node)) for node in item.nodes)
                if pagination is not None and text_capacity is not None and length > text_capacity // 2:
                    size = max(1, min(256, text_capacity // 8))
                    context.work.consume(1 + length // size)
                    fragments_items.extend(
                        replace(item, nodes=nodes, continuation=index > 0)
                        for index, nodes in enumerate(text_fragments(item.nodes, size))
                    )
                else:
                    fragments_items.append(item)
                if pagination is not None and pagination.per and (index + 1) % pagination.per == 0:
                    required.append(len(fragments_items))
            sequence = TextSequenceRealization(tuple(fragments_items), separator=line_separator)
            contract = None
            if pagination is not None:
                assert pagination.key is not None
                contract = RegionContract(
                    pagination.key,
                    RegionNavigation(occurrence, disabled_controls, pagination.footer, pagination.page, pagination.key),
                    min_fill=pagination.min_fill,
                    widows=pagination.widows,
                    owner=pagination.page,
                    cursor_key=pagination.key,
                    required_ends=tuple(required),
                )
            return RealizationOffer(
                occurrence,
                key,
                sequence if pagination is not None else line_payload(lines),
                fragments=FragmentContract(
                    tuple(frozenset({item.reference}) for item in fragments_items),
                    tuple(range(1, len(fragments_items) + 1)),
                    sequence.fragment,
                    continuations=pagination is not None,
                )
                if fragments_items
                else None,
                region=contract,
                preference_priority=line_priority,
                preference=preference,
                effects=effects,
            )

        full = line_offer(items, "lines")
        line_offers = [full]
        levels = [0] * len(items)
        if isinstance(overflow, Spill | Condense | Paginate):
            while eligible := [
                index
                for index, line in enumerate(value.lines)
                if isinstance(line, Alt) and levels[index] < len(line.fallbacks)
            ]:
                context.work.consume(len(items))
                largest = max(
                    eligible, key=lambda index: (sum(len(tree.plain(node)) for node in items[index].nodes), -index)
                )
                line = value.lines[largest]
                assert isinstance(line, Alt)
                nodes = resolve_text(line.fallbacks[levels[largest]], context.localization).nodes
                levels[largest] += 1
                items = (*items[:largest], replace(items[largest], nodes=nodes), *items[largest + 1 :])
                replacement_effects = occurrence.scope_effects(
                    tuple(Replacement(item.reference) for index, item in enumerate(items) if levels[index])
                )
                line_offers.append(
                    line_offer(items, f"lines-alternate-{len(line_offers)}", len(line_offers), replacement_effects)
                )
            full = line_offers[-1]
            payload = line_payload(items)
        if isinstance(overflow, Paginate):
            return tuple(line_offers)
        if isinstance(overflow, Truncate | Condense):
            discovered = text_offers(
                full, payload, overflow.keep if isinstance(overflow, Truncate) else "head", value.priority
            )
            if isinstance(discovered, OfferDiscovery):
                return replace(discovered, offers=(*line_offers[:-1], *discovered.offers))
            lengths = tuple(sum(len(tree.plain(node)) for node in item.nodes) for item in items)
            total = sum(lengths) + max(0, len(items) - 1) * len(value.join)
            shortened_lines: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = list(line_offers[:-1])
            for offer in discovered:
                operation = offer.payload.shortening if isinstance(offer.payload, TextRealization) else None
                if operation is None:
                    shortened_lines.append(offer)
                    continue
                start, end = (
                    (0, operation.max_chars) if operation.keep == "head" else (total - operation.max_chars, total)
                )
                offset = 0
                line_effects: list[Shortening] = []
                context.work.consume(len(items))
                for item, length in zip(items, lengths, strict=True):
                    retained = max(0, min(end, offset + length) - max(start, offset))
                    if retained < length:
                        line_effects.append(Shortening(item.reference, retained, operation.keep))
                    offset += length + len(value.join)
                shortened_lines.append(
                    replace(
                        offer,
                        preference=full.preference + offer.preference,
                        effects=(*full.effects, *occurrence.scope_effects(tuple(line_effects))),
                    )
                )
            return tuple(shortened_lines)
        if isinstance(overflow, Never) or not items:
            return (full,)
        offers = list(line_offers)
        line_priorities = tuple(line.priority if isinstance(line, Alt) else 0 for line in value.lines)
        drop_order = sorted(
            range(len(items)),
            key=lambda index: (
                line_priorities[index],
                -index,
            ),
        )
        for removed in range(1, len(items) + 1) if isinstance(overflow, Spill) else (len(items),):
            dropped = set(drop_order[:removed])
            retained = tuple(item for index, item in enumerate(items) if index not in dropped)
            context.work.consume(len(items))
            effects = occurrence.scope_effects(
                tuple(
                    Omission(item.reference) if index in dropped else Replacement(item.reference)
                    for index, item in enumerate(items)
                    if index in dropped or (isinstance(overflow, Spill) and levels[index])
                )
            )
            content = line_payload(retained).nodes
            if isinstance(overflow, Spill):
                content = (
                    *content,
                    *((tree.literal(value.join),) if content else ()),
                    *resolve_text(context.chrome.and_n_more(removed), context.localization).nodes,
                )
            offers.append(
                replace(
                    full,
                    key=f"spill-{removed}",
                    payload=TextRealization(content),
                    effects=effects,
                    preference=full.preference + removed,
                    fragments=None,
                )
            )
        if isinstance(overflow, Spill):
            offers.append(
                replace(
                    offers[-1],
                    key="spill-empty",
                    payload=TextRealization(()),
                    preference=full.preference + len(items) + 1,
                )
            )
        return tuple(offers)
    if isinstance(value, primitive.Text | primitive.Heading | primitive.Footer | primitive.Code):
        overflow = Truncate(keep) if keep is not None else value.overflow
        if not native_controls or not isinstance(overflow, Never | Truncate | Alts | Drop | Paginate):
            return ()
        payload = TextRealization(resolve_text(value.content, context.localization).nodes)
        if isinstance(value, primitive.Heading):
            payload = replace(payload, role="heading", level=value.level)
        elif isinstance(value, primitive.Footer):
            payload = replace(payload, role="note")
        elif isinstance(value, primitive.Code):
            payload = replace(payload, role="code", language=value.lang)
        if isinstance(overflow, Paginate):
            if overflow.key is None:
                return OfferDiscovery((), DiscoveryCompletion.UNSUPPORTED, "unkeyed primitive pagination")
            nodes = payload.nodes
            if isinstance(value, primitive.Code):
                nodes = (tree.literal("".join(tree.plain(node) for node in nodes).replace("```", "``\u200b`")),)
            plain = "".join(tree.plain(node) for node in nodes)
            safe_ends = frozenset((0, *text_ends(nodes)))
            context.work.consume(len(plain) + len(safe_ends))
            spans: list[tuple[int, int]] = []
            start = offset = 0
            if overflow.boundary:
                while (offset := plain.find(overflow.boundary, offset)) >= 0:
                    after = offset + len(overflow.boundary)
                    if offset in safe_ends and after in safe_ends:
                        spans.append((start, offset))
                        start = after
                    offset = after
            spans.append((start, len(plain)))
            parts: list[ReferencedText] = []
            ends: list[int] = []
            for index, (start, end) in enumerate(spans):
                chunk = _slice(nodes, start, end)
                pieces = (
                    text_fragments(chunk, max(1, min(256, text_capacity // 8)))
                    if text_capacity is not None and end - start > text_capacity // 2
                    else (chunk,)
                )
                parts.extend(
                    ReferencedText(occurrence.reference, piece, continuation=i > 0) for i, piece in enumerate(pieces)
                )
                if overflow.per is not None and (index + 1) % overflow.per == 0:
                    ends.append(len(parts))
            prefix = (
                f"```{value.lang}\n"
                if isinstance(value, primitive.Code)
                else "#" * value.level + " "
                if isinstance(value, primitive.Heading)
                else "-# "
                if isinstance(value, primitive.Footer)
                else ""
            )
            sequence = TextSequenceRealization(
                tuple(parts),
                overflow.boundary,
                prefix,
                "\n```" if isinstance(value, primitive.Code) else "",
                literal=isinstance(value, primitive.Code),
            )
            return (
                RealizationOffer(
                    occurrence,
                    "native-text-pages",
                    sequence,
                    fragments=FragmentContract(
                        tuple(frozenset({occurrence.reference}) for _ in parts),
                        tuple(range(1, len(parts) + 1)),
                        sequence.fragment,
                        continuations=True,
                    ),
                    region=RegionContract(
                        overflow.key,
                        RegionNavigation(occurrence, disabled_controls, overflow.footer, overflow.page, overflow.key),
                        min_fill=overflow.min_fill,
                        widows=overflow.widows,
                        owner=overflow.page,
                        cursor_key=overflow.key,
                        required_ends=tuple(ends),
                    ),
                ),
            )
        full = RealizationOffer(occurrence, "native-text", payload, preference_priority=value.priority)
        if isinstance(overflow, Drop):
            return (
                full,
                replace(
                    full,
                    key="drop",
                    payload=replace(payload, nodes=(), role="paragraph"),
                    preference=1,
                    effects=tuple(Omission(source) for source in sorted(occurrence.sources, key=lambda ref: ref.keys)),
                ),
            )
        if isinstance(overflow, Alts):
            effects = occurrence.scope_effects((Replacement(occurrence.reference),))
            replacements = tuple(
                replace(
                    full,
                    key=f"alternate-{index}",
                    preference=index,
                    payload=replace(payload, nodes=resolve_text(content, context.localization).nodes),
                    effects=effects,
                )
                for index, content in enumerate(overflow.ladder, 1)
            )
            context.work.consume(len(replacements))
            last = replacements[-1]
            shortened = text_offers(last, last.payload, "head", value.priority)
            if isinstance(shortened, OfferDiscovery):
                return replace(shortened, offers=(full, *replacements[:-1], *shortened.offers))
            return (
                full,
                *replacements,
                *(replace(offer, preference=last.preference + offer.preference) for offer in shortened[1:]),
            )
        return text_offers(
            full,
            payload,
            overflow.keep if isinstance(overflow, Truncate) else None,
            value.priority,
        )
    if isinstance(value, primitive.Gallery | primitive.MediaCollection):
        if not native_v2:
            return ()
        context.work.consume(len(value.items))
        gallery = GalleryRealization(
            tuple(
                scene.GalleryItem(
                    item.url or (f"attachment://{item.asset_name}" if item.asset_name is not None else ""),
                    resolve_text(item.description, context.localization).plain()
                    if item.description is not None
                    else None,
                    item.spoiler,
                    item.asset_name,
                )
                for raw in value.items
                for item in (primitive.normalize_gallery_item(raw),)
            ),
            collection=isinstance(value, primitive.MediaCollection),
        )
        refs = occurrence.graph.children.get(occurrence.reference, ())
        return (
            RealizationOffer(
                occurrence,
                "native-gallery",
                gallery,
                fragments=FragmentContract(
                    tuple(frozenset({ref}) for ref in refs), tuple(range(1, len(refs) + 1)), gallery.fragment
                )
                if gallery.collection and refs
                else None,
            ),
        )
    if isinstance(value, primitive.File):
        return (
            (
                RealizationOffer(
                    occurrence,
                    "native-file",
                    FileRealization(scene.File(value.asset_key, value.name, value.media_type, value.spoiler)),
                ),
            )
            if native_v2
            else ()
        )
    if isinstance(value, primitive.Thumbnail):
        if not native_v2:
            return ()
        description = resolve_text(value.description, context.localization).plain() if value.description else None
        return (
            RealizationOffer(
                occurrence, "thumbnail", ThumbnailRealization(scene.Thumbnail(value.url, description, value.spoiler))
            ),
        )
    if isinstance(value, primitive.Sep):
        return (
            (
                RealizationOffer(
                    occurrence, "separator", SeparatorRealization(scene.Separator(value.large, value.visible))
                ),
            )
            if native_v2
            else ()
        )
    if isinstance(value, primitive.Row | primitive.ControlGroup | primitive.Section | primitive.Panel):
        if not native_controls or (isinstance(value, primitive.Section | primitive.Panel) and not native_v2):
            return ()
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for ref in occurrence.graph.children.get(occurrence.reference, ())
        )
        return (
            RealizationOffer(
                occurrence,
                "native-layout",
                RowRealization()
                if isinstance(value, primitive.Row)
                else ActionsRealization()
                if isinstance(value, primitive.ControlGroup)
                else NativeSectionRealization()
                if isinstance(value, primitive.Section)
                else PanelRealization(value.accent, spoiler=value.spoiler),
                children,
                flow=isinstance(value, primitive.ControlGroup | primitive.Panel),
            ),
        )
    if isinstance(
        value,
        primitive.Button
        | primitive.RoutedButton
        | primitive.LinkButton
        | primitive.PremiumButton
        | primitive.SelectMenu
        | primitive.RoutedSelect
        | primitive.EntitySelect,
    ):
        if not native_controls:
            return ()
        candidate = navigation_candidate(value, occurrence, context)
        return (replace(candidate.offer, key="native-control", interaction=True),)
    if isinstance(value, sem.Themed):
        context = replace(context, palette=value.palette)
    if isinstance(value, sem.Budgeted | primitive.Budget | sem.Paged) and not character_budgets:
        return ()
    if isinstance(value, sem.FallbackContent):
        refs = occurrence.graph.children[occurrence.reference]
        primary = refs[0]
        sources = (
            occurrence.origin.references(primary) if occurrence.origin is not None else occurrence.graph.atoms(primary)
        )
        context.work.consume(len(sources) + len(refs))
        effects = tuple(Replacement(source) for source in sorted(sources or {primary}, key=lambda ref: ref.keys))
        return tuple(
            RealizationOffer(
                occurrence,
                f"fallback-{index}",
                FlowRealization(),
                (
                    SemanticOccurrence(
                        OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                        occurrence.graph,
                        ref,
                        occurrence.placement,
                        occurrence.origin,
                        palette=context.palette,
                        enclosing=occurrence.enclosing,
                    ),
                ),
                effects=effects if index else (),
                preference=index,
                flow=True,
            )
            for index, ref in enumerate(refs)
        )
    if isinstance(value, sem.OptionalContent):
        ref = occurrence.graph.children[occurrence.reference][0]
        child = SemanticOccurrence(
            OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
            occurrence.graph,
            ref,
            occurrence.placement,
            occurrence.origin,
            palette=context.palette,
            enclosing=occurrence.enclosing,
        )
        sources = sorted(occurrence.sources, key=lambda source: source.keys)
        context.work.consume(len(sources))
        return (
            RealizationOffer(
                occurrence, "retain", FlowRealization(), (child,), flow=True, preference_priority=value.importance
            ),
            RealizationOffer(
                occurrence,
                "omit",
                FlowRealization(),
                effects=tuple(Omission(source) for source in sources),
                preference=1,
                flow=True,
                preference_priority=value.importance,
            ),
        )
    if isinstance(value, Alternatives):
        choices: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = []
        for choice in value.choices:
            context.work.consume()
            checked = check_alternative(value, choice, consume=context.work.consume)
            origin = SourceScope(checked, occurrence.reference, occurrence.origin)
            root = next(ref for ref, unit in checked.presentation.units.items() if unit.parent is None)
            child = SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, "@presentation")),
                checked.presentation,
                root,
                occurrence.placement,
                origin,
                palette=context.palette,
                enclosing=occurrence,
            )
            effects = tuple(
                replace(effect, source=source)
                for effect in choice.fidelity.effects
                for source in sorted(
                    origin.source_references(checked.source.resolve(effect.source)), key=lambda ref: ref.keys
                )
            )
            choices.append(
                RealizationOffer(
                    occurrence, choice.key, FlowRealization(), (child,), effects, choice.preference, flow=True
                )
            )
        return tuple(choices)
    if isinstance(value, sem.List):
        items = tuple(
            ReferencedText(
                ref,
                resolve_text(item.content, context.localization).nodes,
                f"{index + 1}. " if value.ordered else "• ",
                ordinal=index + 1,
            )
            for index, (ref, item) in enumerate(
                zip(occurrence.graph.children.get(occurrence.reference, ()), value.items, strict=True)
            )
        )
        per = value.page_size
        required: list[int] = []
        expanded: list[ReferencedText] = []
        capacity = text_capacity
        for index, item in enumerate(items):
            plain = "".join(tree.plain(node) for node in item.nodes)
            if capacity is not None and len(plain) > capacity // 2:
                size = max(1, min(256, capacity // 8))
                context.work.consume(1 + len(plain) // size)
                expanded.extend(
                    replace(
                        item,
                        nodes=nodes,
                        prefix=item.prefix if index == 0 else "",
                        continuation=index > 0,
                    )
                    for index, nodes in enumerate(text_fragments(item.nodes, size))
                )
            else:
                expanded.append(item)
            if per is not None and (index + 1) % per == 0:
                required.append(len(expanded))
        items = tuple(expanded)
        payload = TextSequenceRealization(items, list_ordered=value.ordered)
        units = tuple(frozenset({item.reference}) for item in items)
        fragments = (
            FragmentContract(units, tuple(range(1, len(items) + 1)), payload.fragment, continuations=True)
            if items
            else None
        )
        contract = RegionContract(
            value.key,
            RegionNavigation(
                occurrence,
                disabled_controls,
                None,
                value.page,
                value.key,
            ),
            owner=value.page,
            cursor_key=value.key,
            required_ends=tuple(required),
        )
        complete = RealizationOffer(occurrence, "text-pages", payload, fragments=fragments, region=contract)
        if not spill or not value.items:
            return (complete,)
        originals = tuple(
            ReferencedText(
                ref,
                resolve_text(item.content, context.localization).nodes,
                f"{index + 1}. " if value.ordered else "• ",
                ordinal=index + 1,
            )
            for index, (ref, item) in enumerate(
                zip(occurrence.graph.children[occurrence.reference], value.items, strict=True)
            )
        )
        order = sorted(range(len(originals)), key=lambda index: (value.items[index].importance, -index))
        spilled_lists = [complete]
        for count in range(1, len(originals) + 1):
            context.work.consume(len(originals))
            omitted = set(order[:count])
            retained = tuple(item for index, item in enumerate(originals) if index not in omitted)
            shortened_sequence = replace(
                payload,
                items=retained,
                notice=resolve_text(context.chrome.and_n_more(count), context.localization).nodes,
            )
            spilled_lists.append(
                replace(
                    complete,
                    key=f"spill-{count}",
                    payload=shortened_sequence,
                    preference=count,
                    effects=occurrence.scope_effects(
                        tuple(Omission(item.reference) for index, item in enumerate(originals) if index in omitted)
                    ),
                    fragments=FragmentContract(
                        tuple(frozenset({item.reference}) for item in retained),
                        tuple(range(1, len(retained) + 1)),
                        shortened_sequence.fragment,
                    )
                    if retained
                    else None,
                    region=replace(
                        contract, required_ends=tuple(range(per, len(retained) + 1, per)) if per is not None else ()
                    ),
                )
            )
        spilled_lists.append(
            replace(
                spilled_lists[-1],
                key="spill-empty",
                payload=replace(payload, items=(), notice=()),
                preference=len(originals) + 1,
            )
        )
        return tuple(spilled_lists)
    if isinstance(value, TextPresentation):
        if occurrence.origin is None:
            message = "source-referenced text must belong to a checked alternative"
            raise LayoutInvariantError(message)
        resolved = resolve_presentation_text(
            occurrence.origin.checked, occurrence.reference, localization=context.localization
        )
        if isinstance(resolved, UnsupportedText):
            return ()
        return (RealizationOffer(occurrence, "text", TextRealization(resolved.nodes, resolved)),)
    if isinstance(value, ItemSummary):
        return (
            RealizationOffer(
                occurrence,
                "item-heading",
                TextRealization(
                    resolve_text(value.item.label.content, context.localization).nodes, role="heading", level=3
                ),
            ),
        )
    if isinstance(value, ItemBack):
        key = _control_key(occurrence, context)
        label = "".join(tree.plain(node) for node in resolve_text(context.chrome.back, context.localization).nodes)
        return (
            RealizationOffer(
                occurrence,
                "item-back",
                ControlRealization(label, action=key),
                interaction=True,
                binding=ActionBinding(
                    key, CloseItem(ItemCommit(value.items.opened, value.items.key, context.presentation))
                ),
            ),
        )
    if isinstance(value, ItemBranch):
        refs = occurrence.graph.children.get(occurrence.reference, ())
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                Placement.HEADER if index == 0 and len(refs) > 1 else occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for index, ref in enumerate(refs)
        )
        return (RealizationOffer(occurrence, "item-detail", FlowRealization(), children, flow=True),)
    if isinstance(value, sem.Items):
        opened = opened_item(value, context.presentation)
        refs = occurrence.graph.children.get(occurrence.reference, ())
        context.work.consume(len(refs))
        if opened is not None:
            visible = tuple(ref for ref in refs if ref.keys[-1] in (opened, "@back"))
            children = tuple(
                SemanticOccurrence(
                    OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                    occurrence.graph,
                    ref,
                    Placement.FOOTER if ref.keys[-1] == "@back" else occurrence.placement,
                    occurrence.origin,
                    palette=context.palette,
                    enclosing=occurrence.enclosing,
                )
                for ref in visible
            )
            cursor_key = f"{value.key}.{opened}.detail"
            return (
                RealizationOffer(
                    occurrence,
                    "items.opened",
                    FlowRealization(),
                    children,
                    flow=True,
                    region=RegionContract(
                        cursor_key,
                        RegionNavigation(occurrence, disabled_controls, cursor_key=cursor_key, default_controls=True),
                        cursor_key=cursor_key,
                    ),
                ),
            )
        if not value.items:
            return (RealizationOffer(occurrence, "items.empty", FlowRealization(), flow=True),)
        key = _control_key(occurrence, context)
        summaries: list[ReferencedText] = []
        item_options: list[scene.Option] = []
        for item, ref in zip(value.items, refs[:-1], strict=True):
            label = resolve_text(item.label.content, context.localization).nodes
            nodes = label
            if item.summary is not None:
                nodes += (tree.literal(" — "), *resolve_text(item.summary, context.localization).nodes)
            summaries.append(ReferencedText(SemanticRef((*ref.keys, "@summary")), nodes))
            item_options.append(scene.Option("".join(tree.plain(node) for node in label), item.key))
        payload = SelectRealization(
            scene.Select(tuple(item_options), key, "Choose an item"), summaries=tuple(summaries)
        )
        cursor_key = f"{value.key}.items"
        return (
            RealizationOffer(
                occurrence,
                "items.overview",
                payload,
                interaction=True,
                binding=ActionBinding(key, FocusItem(ItemCommit(value.opened, value.key, context.presentation))),
                fragments=FragmentContract(
                    tuple(frozenset({summary.reference}) for summary in summaries),
                    tuple(range(1, len(summaries) + 1)),
                    payload.fragment,
                ),
                region=RegionContract(
                    cursor_key,
                    RegionNavigation(occurrence, disabled_controls, cursor_key=cursor_key, default_controls=True),
                    cursor_key=cursor_key,
                ),
            ),
        )
    if isinstance(value, DisclosureToggle):
        details = value.details
        open_ = disclosure_state(details, context.presentation)
        key = _control_key(occurrence, context)
        label = "".join(tree.plain(node) for node in resolve_text(details.summary.content, context.localization).nodes)
        return (
            RealizationOffer(
                occurrence,
                "disclosure-toggle",
                ControlRealization(label, action=key),
                interaction=True,
                binding=ActionBinding(key, ToggleDetails(details, open_, context.presentation)),
            ),
        )
    if isinstance(value, sem.Details):
        open_ = disclosure_state(value, context.presentation)
        refs = occurrence.graph.children.get(occurrence.reference, ())
        visible = refs if open_ else refs[:1]
        context.work.consume(len(visible))
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                Placement.HEADER if open_ and len(refs) > 1 and index == 0 else occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for index, ref in enumerate(visible)
        )
        return (RealizationOffer(occurrence, "disclosure", DisclosureRealization(open_), children, flow=True),)
    if isinstance(value, NavigationChoice):
        navigation, option = value.navigation, value.option
        current = navigation_current(navigation, context.presentation)
        key = _control_key(occurrence, context)
        label = "".join(tree.plain(node) for node in resolve_text(option.label, context.localization).nodes)
        return (
            RealizationOffer(
                occurrence,
                "destination",
                ControlRealization(
                    label,
                    action=key,
                    style=ActionStyle.PRIMARY if option.key == current else ActionStyle.SECONDARY,
                    current=option.key == current,
                ),
                interaction=True,
                binding=ActionBinding(
                    key,
                    GoToDestination(
                        NavigationCommit(navigation.current, navigation.key, context.presentation), option.key
                    ),
                    label=option.label,
                ),
            ),
        )
    if isinstance(value, sem.Navigation):
        refs = occurrence.graph.children.get(occurrence.reference, ())
        available = tuple((ref, option) for ref, option in zip(refs, value.options, strict=True) if option.available)
        context.work.consume(len(refs))
        cursor_key = f"{value.key}.destinations"
        contract = RegionContract(
            cursor_key,
            RegionNavigation(occurrence, disabled_controls, cursor_key=cursor_key, default_controls=True),
            cursor_key=cursor_key,
        )
        individual = RealizationOffer(
            occurrence,
            "navigation.individual",
            ActionsRealization(navigation=True),
            tuple(
                SemanticOccurrence(
                    OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                    occurrence.graph,
                    ref,
                    occurrence.placement,
                    occurrence.origin,
                    palette=context.palette,
                    enclosing=occurrence.enclosing,
                )
                for ref, _ in available
            ),
            flow=True,
            region=contract,
        )
        if not available:
            return (individual,)
        current = navigation_current(value, context.presentation)
        key = _control_key(occurrence, context)
        payload = SelectRealization(
            scene.Select(
                tuple(
                    scene.Option(
                        "".join(tree.plain(node) for node in resolve_text(option.label, context.localization).nodes),
                        option.key,
                        default=option.key == current,
                    )
                    for _, option in available
                ),
                key,
            ),
            navigation=True,
        )
        grouped = RealizationOffer(
            occurrence,
            "navigation.grouped",
            payload,
            interaction=True,
            binding=ActionBinding(
                key, SelectDestination(NavigationCommit(value.current, value.key, context.presentation))
            ),
            fragments=FragmentContract(
                tuple(frozenset({ref}) for ref, _ in available), tuple(range(1, len(available) + 1)), payload.fragment
            ),
            region=contract,
        )
        prefer_grouped = value.display == sem.NavigationDisplay.GROUPED or (
            value.display == sem.NavigationDisplay.AUTO and len(available) > 5
        )
        return (
            (grouped, replace(individual, preference=1))
            if prefer_grouped
            else (individual, replace(grouped, preference=1))
        )
    if isinstance(value, sem.Grid):
        refs = occurrence.graph.children[occurrence.reference]
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                occurrence.placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for ref in refs
        )
        contract = RegionContract(
            value.key + ".cells",
            RegionNavigation(occurrence, disabled_controls, owner=FIRST_PAGE, cursor_key=value.key + ".cells"),
            cursor_key=value.key + ".cells",
        )
        buttons = RealizationOffer(
            occurrence,
            "grid.buttons",
            ActionsRealization(columns=value.columns, grid_key=value.key),
            children,
            flow=True,
            region=contract,
            preference_priority=int(value.flexibility),
        )
        button_offers = (
            (buttons,)
            if (grid_columns is None or value.columns <= grid_columns)
            and (disabled_controls or all(cell.available for cell in value.cells))
            else ()
        )
        available = tuple(index for index, cell in enumerate(value.cells) if cell.available)
        labels = tuple(resolve_text(cell.label, context.localization).plain() for cell in value.cells)
        grid_summaries = tuple(
            ReferencedText(
                ref, (tree.literal(f"{_coordinate(index, value.columns)} [{' ' if cell.available else 'x'}] {label}"),)
            )
            for index, (ref, cell, label) in enumerate(zip(refs, value.cells, labels, strict=True))
        )
        if not available:
            payload = TextSequenceRealization(grid_summaries)
            return (
                *button_offers,
                RealizationOffer(
                    occurrence,
                    "grid.readonly",
                    payload,
                    preference=1,
                    fragments=FragmentContract(
                        tuple(frozenset({ref}) for ref in refs), tuple(range(1, len(refs) + 1)), payload.fragment
                    ),
                    region=contract,
                    preference_priority=int(value.flexibility),
                ),
            )
        options = tuple(
            scene.Option(f"{_coordinate(index, value.columns)} — {labels[index]}", value.cells[index].key)
            for index in available
        )
        action = _control_key(occurrence, context)
        # Attach unavailable cells to a neighbouring available cell. Every selector
        # fragment then retains its contextual labels and at least one valid option.
        boundaries = (0, *available[1:], len(refs))

        def grid_fragment(start: int, end: int) -> Realization:
            return SelectRealization(
                scene.Select(options[start:end], action), summaries=grid_summaries[boundaries[start] : boundaries[end]]
            )

        select = RealizationOffer(
            occurrence,
            "grid.coordinates",
            grid_fragment(0, len(available)),
            preference=1,
            fragments=FragmentContract(
                tuple(frozenset(refs[start:end]) for start, end in pairwise(boundaries)),
                tuple(range(1, len(available) + 1)),
                grid_fragment,
            ),
            region=contract,
            preference_priority=int(value.flexibility),
            interaction=True,
            binding=ActionBinding(action, value.on_pick),
        )
        return (*button_offers, select)
    if isinstance(value, sem.Entities):
        previous = (
            value.selection.value
            if isinstance(value.selection, Controlled)
            else tuple(
                EntityRef.decode(key)
                for key in context.presentation.selection(
                    value.key, initial=tuple(ref.encode() for ref in value.selection.initial)
                ).selected
            )
        )
        key = _control_key(occurrence, context)
        commit = EntityCommit(value.selection, value.key, previous, context.presentation)
        options = tuple(
            scene.Option(
                "".join(tree.plain(node) for node in resolve_text(choice.label, context.localization).nodes),
                choice.ref.encode(),
                "".join(tree.plain(node) for node in resolve_text(choice.description, context.localization).nodes)
                if choice.description is not None
                else None,
                choice.ref in previous,
            )
            for choice in value.choices
            if choice.available
        )
        placeholder = (
            "".join(tree.plain(node) for node in resolve_text(value.placeholder, context.localization).nodes)
            if value.placeholder is not None
            else None
        )
        native = RealizationOffer(
            occurrence,
            "entity",
            EntityRealization(
                scene.EntitySelect(
                    value.entity_type,
                    key,
                    placeholder,
                    previous,
                    value.conversation_types,
                    value.minimum,
                    value.maximum,
                ),
                options,
            ),
            interaction=True,
            binding=ActionBinding(key, SelectEntities(commit), ActionMode.EXCLUSIVE),
        )
        if not value.choices:
            return (native,)
        by_key = {choice.ref.encode(): choice.ref for choice in value.choices if choice.available}
        selected = tuple(ref.encode() for ref in previous if ref.encode() in by_key)
        fallback = RealizationOffer(
            occurrence,
            "entity-choices",
            SelectRealization(scene.Select(options, key, placeholder, value.minimum, value.maximum)),
            preference=1,
            interaction=True,
            binding=ActionBinding(
                key,
                SelectChoices(
                    ChoiceCommit(
                        Controlled(selected, SelectEntityFallback(commit, by_key)),
                        value.key,
                        selected,
                        context.presentation,
                    )
                ),
                ActionMode.EXCLUSIVE,
            ),
        )
        return (native, fallback)
    if isinstance(value, sem.Choices | sem.RoutedChoices):
        previous = (
            value.selected
            if isinstance(value, sem.RoutedChoices)
            else value.selection.value
            if isinstance(value.selection, Controlled)
            else context.presentation.selection(value.key, initial=value.selection.initial).selected
        )
        options = tuple(
            scene.Option(
                "".join(tree.plain(node) for node in resolve_text(choice.label, context.localization).nodes),
                choice.key,
                "".join(tree.plain(node) for node in resolve_text(choice.description, context.localization).nodes)
                if choice.description is not None
                else None,
                choice.key in previous,
            )
            for choice in value.choices
            if choice.available
        )
        key = _control_key(occurrence, context)
        if isinstance(value, sem.RoutedChoices):
            placeholder = (
                "".join(tree.plain(node) for node in resolve_text(value.placeholder, context.localization).nodes)
                if value.placeholder is not None
                else None
            )
            payload = SelectRealization(
                scene.RoutedSelect(
                    options, value.route_id, placeholder, value.minimum, value.maximum, not value.available
                )
            )
            binding = None
        else:
            payload = SelectRealization(scene.Select(options, key, min_values=value.minimum, max_values=value.maximum))
            binding = ActionBinding(
                key,
                SelectChoices(ChoiceCommit(value.selection, value.key, previous, context.presentation)),
                ActionMode.EXCLUSIVE,
            )
        return (RealizationOffer(occurrence, "select", payload, interaction=True, binding=binding),)
    if isinstance(value, sem.ActionControl | sem.RoutedActionControl | sem.Link | sem.FormTrigger | sem.Toggle):
        # Wire IDs follow semantic ownership, independently of presentation keys.
        key = _control_key(occurrence, context)
        label = "".join(tree.plain(node) for node in resolve_text(value.label, context.localization).nodes)
        binding = None
        form = None
        match value:
            case sem.Toggle():
                on = toggle_state(value, context.presentation)
                state_label = value.on_label if on else value.off_label
                if state_label is None:
                    state_label = context.chrome.on if on else context.chrome.off
                label += ": " + "".join(
                    tree.plain(node) for node in resolve_text(state_label, context.localization).nodes
                )
                payload = ControlRealization(
                    label,
                    action=key,
                    style=_button_style(value.tone, sem.Emphasis.NORMAL),
                    disabled=not value.available,
                )
                binding = ActionBinding(
                    key, FlipToggle(value, on, context.presentation), ActionMode.EXCLUSIVE, label=label
                )
            case sem.ActionControl():
                payload = ControlRealization(
                    label,
                    action=key,
                    style=_button_style(value.tone, value.emphasis),
                    disabled=not value.available,
                    mode=value.mode,
                )
                binding = ActionBinding(
                    key,
                    value.on_trigger,
                    value.mode,
                    guard=value.guard,
                    busy=value.busy,
                    label=value.label,
                    record=value.record,
                )
            case sem.FormTrigger():
                if Capability.FORMS_MODAL not in context.capabilities:
                    return ()
                form = FormBinding(key, value.spec, value.on_submit, value.mode, value.label, value.record)
                binding = ActionBinding(
                    key,
                    PresentForm(value.spec, key, value.on_submit, value.mode, value.label, value.record),
                    value.mode,
                    guard=value.guard,
                    label=value.label,
                )
                payload = ControlRealization(
                    label, action=key, style=_button_style(value.tone, value.emphasis), mode=value.mode
                )
            case sem.RoutedActionControl():
                payload = ControlRealization(
                    label,
                    route=value.route_id,
                    style=_button_style(value.tone, value.emphasis),
                    disabled=not value.available,
                )
            case sem.Link():
                payload = ControlRealization(label, url=value.url)
        parent = occurrence.graph.units[occurrence.reference].parent
        keep_next = False
        if parent is not None and isinstance(grid := occurrence.graph.units[parent].value, sem.Grid):
            index = occurrence.graph.children[parent].index(occurrence.reference)
            keep_next = (index + 1) % grid.columns != 0 and index + 1 < len(grid.cells)
        return (
            RealizationOffer(
                occurrence, "button", payload, interaction=True, binding=binding, form=form, keep_with_next=keep_next
            ),
        )
    if isinstance(value, sem.Metric):
        label = resolve_text(value.label, context.localization).nodes
        rendered = f"{value.value}{' ' + value.unit if value.unit is not None else ''}"
        nodes = (
            tree.Node("strong", children=(*label, tree.literal(":")), tag="strong", markup="**"),
            tree.literal(" " + rendered),
        )
        return (RealizationOffer(occurrence, "metric", MetricRealization(nodes, label=label, value=rendered)),)
    if isinstance(value, sem.Timestamp | sem.ZonedTimestamp | primitive.Time | primitive.ZonedTime):
        if isinstance(value, primitive.Time | primitive.ZonedTime) and not native_controls:
            return ()
        label = (
            resolve_text(value.label, context.localization).nodes
            if isinstance(value, sem.Timestamp | sem.ZonedTimestamp) and value.label is not None
            else ()
        )
        if isinstance(value, sem.Timestamp | primitive.Time):
            instant = value.instant
            style = sem.TimeStyle(value.style).value
            timestamp = timestamp_text(instant, style=style)
            time_nodes = resolve_text(timestamp, context.localization).nodes
            display = instant.isoformat()
            timezone = None
        else:
            instant = value.value.instant
            style = None
            display = value.value.isoformat()
            time_nodes = (tree.literal(display),)
            timezone = value.value.timezone
        prefix = (
            (tree.Node("strong", children=(*label, tree.literal(":")), tag="strong", markup="**"), tree.literal(" "))
            if label
            else ()
        )
        if isinstance(value, primitive.Time | primitive.ZonedTime) and value.prefix is not None:
            prefix = resolve_text(value.prefix, context.localization).nodes
            label = prefix
        return (
            RealizationOffer(
                occurrence,
                "time",
                TimeRealization(
                    (*prefix, *time_nodes),
                    instant=instant.isoformat(),
                    display=display,
                    style=style,
                    timezone=timezone,
                    label=label,
                    prefix=prefix,
                    native_prefix=value.prefix if isinstance(value, primitive.Time | primitive.ZonedTime) else None,
                ),
            ),
        )
    if isinstance(value, sem.Status):
        nodes = (tree.literal(value.tone.status_marker), *resolve_text(value.content, context.localization).nodes)
        return (
            RealizationOffer(
                occurrence,
                "status",
                StatusRealization(
                    nodes,
                    colour=context.palette.tone(value.tone),
                    tone=value.tone.base.value,
                    emphasis=value.emphasis.value,
                ),
            ),
        )
    if isinstance(value, sem.ProgressBar):
        label = (
            "".join(tree.plain(node) for node in resolve_text(value.label, context.localization).nodes)
            if value.label is not None
            else None
        )
        # Text-only targets retain the authored quantities, not just a rounded percentage.
        text = f"{label}: " if label is not None else ""
        ratio = 0.0 if value.maximum <= 0 else max(0.0, min(1.0, value.value / value.maximum))
        filled = round(ratio * 10)
        text += f"{value.value}/{value.maximum} {'█' * filled}{'░' * (10 - filled)} {ratio:.0%}"
        return (
            RealizationOffer(
                occurrence,
                "progress",
                ProgressRealization((tree.literal(text),), value=value.value, maximum=value.maximum, label=label),
            ),
        )
    if isinstance(value, sem.Heading | sem.Note | sem.Code | sem.Quote):
        payload = TextRealization(resolve_text(value.content, context.localization).nodes)
        if isinstance(value, sem.Heading):
            payload = replace(payload, role="heading", level=value.level)
        elif isinstance(value, sem.Note):
            payload = replace(payload, role="note")
        elif isinstance(value, sem.Code):
            payload = replace(payload, role="code", language=value.language)
        else:
            if value.attribution is not None:
                payload = replace(
                    payload,
                    nodes=(
                        *payload.nodes,
                        tree.literal(" — "),
                        *resolve_text(value.attribution, context.localization).nodes,
                    ),
                )
            payload = replace(payload, role="quote")
        return text_offers(
            RealizationOffer(occurrence, "text", payload),
            payload,
            priority=value.importance if isinstance(value, sem.Heading | sem.Note) else sem.Importance.NORMAL,
        )
    if isinstance(value, sem.Field):
        label = resolve_text(value.label, context.localization).nodes
        effects = occurrence.scope_effects((Replacement(SemanticRef((*occurrence.reference.keys, "value"))),))
        context.work.consume(1 + len(value.fallbacks))
        return tuple(
            RealizationOffer(
                occurrence,
                "field" if index == 0 else f"field-fallback-{index}",
                FieldRealization((*label, tree.literal(": "), *nodes), label=label, value=nodes),
                effects=effects if index else (),
                preference=index,
                preference_priority=value.importance if value.fallbacks else None,
            )
            for index, content in enumerate((value.value, *value.fallbacks))
            for nodes in (resolve_text(content, context.localization).nodes,)
        )
    if isinstance(value, sem.Paragraph):
        payload = TextRealization(resolve_text(value.content, context.localization).nodes)
        length = sum(len(tree.plain(node)) for node in payload.nodes)
        fragments = None
        if text_capacity is not None and (length > text_capacity or length > max(128, text_capacity // 2)):
            context.work.consume(length)
            fragments = FragmentContract(
                (frozenset({occurrence.reference}),) * length,
                text_ends(payload.nodes),
                payload.fragment,
                continuations=True,
            )
        return text_offers(
            RealizationOffer(occurrence, "text", payload, fragments=fragments), payload, priority=value.importance
        )
    if isinstance(value, sem.Block) and not panels:
        return ()
    if isinstance(
        value,
        sem.Group
        | sem.Stack
        | sem.Section
        | sem.Article
        | sem.Aside
        | sem.Fields
        | sem.Media
        | sem.Figure
        | sem.Cluster
        | sem.Themed
        | sem.ActionControls
        | sem.ControlGroup
        | sem.Truncated
        | sem.Spilled
        | sem.BestEffort
        | sem.Region
        | sem.Paged
        | sem.Budgeted
        | primitive.Budget
        | primitive.Break
        | StructuralGroup
        | sem.Block
        | Budget
        | sem.Unbreakable
        | sem.KeepWithNext,
    ):
        refs = list(occurrence.graph.children.get(occurrence.reference, ()))
        context.work.consume(len(refs))
        if isinstance(value, sem.Region):
            refs.sort(key=lambda ref: -1 if ref.keys[-1] == "@header" else 1 if ref.keys[-1] == "@footer" else 0)
        placement = occurrence.placement
        if isinstance(value, StructuralGroup):
            if value.part == "header":
                placement = Placement.HEADER
            elif value.part == "footer":
                placement = Placement.FOOTER
        children = tuple(
            SemanticOccurrence(
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])),
                occurrence.graph,
                ref,
                Placement.HEADER
                if (isinstance(value, sem.Region) and ref.keys[-1] == "@header")
                or (isinstance(value, sem.Section | sem.Article) and ref.keys[-1] == "@heading")
                else Placement.FOOTER
                if isinstance(value, sem.Region) and ref.keys[-1] == "@footer"
                else placement,
                occurrence.origin,
                palette=context.palette,
                enclosing=occurrence.enclosing,
            )
            for ref in refs
        )
        if isinstance(value, sem.Section | sem.Article):
            payload = PanelRealization(
                resolve_accent(value.accent, context.palette),
                framed=panels and (nested_panels or not inside_panel),
                thumbnail=value.thumbnail,
                heading="".join(
                    tree.plain(node) for node in resolve_text(value.heading.content, context.localization).nodes
                ),
                kind="section" if isinstance(value, sem.Section) else "article",
            )
        elif isinstance(value, sem.Aside):
            payload = PanelRealization(
                context.palette.tone(value.tone),
                framed=panels and (nested_panels or not inside_panel),
                kind="aside",
                tone=value.tone.base.value,
            )
        elif isinstance(value, sem.Block):
            payload = PanelRealization(resolve_accent(value.accent, context.palette))
        elif isinstance(value, sem.ActionControls | sem.ControlGroup):
            payload = ActionsRealization()
        elif isinstance(value, sem.Fields):
            payload = FieldsRealization()
        else:
            payload = FlowRealization()
        region = None
        if isinstance(value, sem.Region | sem.Paged):
            nested_region = any(
                isinstance(ancestor, sem.Region) and not isinstance(ancestor, RootRegion)
                for ancestor in occurrence.ancestors()
            )
            cursor_key = (
                region_cursor_key(occurrence)
                if (isinstance(value, sem.Region) or nested_region) and not isinstance(value, RootRegion)
                else value.key
            )
            region = RegionContract(
                value.key,
                RegionNavigation(
                    occurrence,
                    disabled_controls,
                    value.footer if isinstance(value, sem.Paged) else None,
                    value.page,
                    cursor_key,
                ),
                value.chars if isinstance(value, sem.Paged) else None,
                value.min_fill if isinstance(value, sem.Paged) else 0,
                value.widows if isinstance(value, sem.Paged) else 1,
                value.page,
                cursor_key,
            )
        container = RealizationOffer(
            occurrence,
            "container",
            payload,
            children,
            flow=not (
                isinstance(value, sem.Unbreakable | sem.Figure)
                or (isinstance(value, primitive.Break) and value.unbreakable)
            ),
            keep_with_next=isinstance(value, sem.KeepWithNext)
            or (isinstance(value, primitive.Break) and value.keep_with_next),
            region=region,
        )
        if isinstance(value, sem.Fields) and spill and value.fields:
            order = sorted(range(len(children)), key=lambda index: (value.fields[index].importance, -index))
            spilled_fields = [container]
            for count in range(1, len(children) + 1):
                context.work.consume(len(children))
                omitted = set(order[:count])
                spilled_fields.append(
                    replace(
                        container,
                        key=f"spill-{count}",
                        children=tuple(child for index, child in enumerate(children) if index not in omitted),
                        payload=FieldsRealization(
                            resolve_text(context.chrome.and_n_more(count), context.localization).nodes
                        ),
                        preference=count,
                        effects=occurrence.scope_effects(
                            tuple(Omission(child.reference) for index, child in enumerate(children) if index in omitted)
                        ),
                    )
                )
            spilled_fields.append(
                replace(
                    spilled_fields[-1], key="spill-empty", payload=FieldsRealization(), preference=len(children) + 1
                )
            )
            return tuple(spilled_fields)
        if isinstance(value, sem.ActionControls) and value.display != sem.ControlDisplay.INDIVIDUAL:
            eligible = tuple(
                item
                for item in value.items
                if isinstance(item, sem.ActionControl)
                and item.available
                and (
                    item.allow_grouping
                    if item.allow_grouping is not None
                    else item.emphasis != sem.Emphasis.STRONG and item.tone.base in (sem.Tone.NEUTRAL, sem.Tone.INFO)
                )
            )
            if len(eligible) == len(value.items) and len(eligible) > 1:
                encoded = json.dumps(sorted(ref.keys for ref in occurrence.sources), ensure_ascii=True)
                key = "composition." + sha256(encoded.encode()).hexdigest()[:32]
                routes = {
                    item.key: ActionBinding(
                        item.key,
                        item.on_trigger,
                        item.mode,
                        guard=item.guard,
                        busy=item.busy,
                        label=item.label,
                        record=item.record,
                    )
                    for item in eligible
                }
                options = tuple(
                    scene.Option(
                        "".join(tree.plain(node) for node in resolve_text(item.label, context.localization).nodes),
                        item.key,
                    )
                    for item in eligible
                )
                grouped = RealizationOffer(
                    occurrence,
                    "actions.grouped",
                    SelectRealization(scene.Select(options, key)),
                    interaction=True,
                    binding=ActionBinding(key, RouteSelection(routes), ActionMode.EXCLUSIVE, routes=routes),
                    preference=0 if value.display == sem.ControlDisplay.GROUPED else 1,
                )
                return (
                    (replace(container, preference=1), grouped)
                    if value.display == sem.ControlDisplay.GROUPED
                    else (container, grouped)
                )
        return (container,)
    if isinstance(value, sem.Table):
        context.work.consume(len(value.columns.columns))
        prefix = occurrence.reference.keys
        columns = tuple(
            ReferencedText(
                SemanticRef((*prefix, "columns", column.key or f"@{i}")),
                resolve_text(column.heading, context.localization).nodes,
            )
            for i, column in enumerate(value.columns.columns)
        )
        rows: list[TableRecord] = []
        for i, row in enumerate(value.rows):
            context.work.consume(1 + len(row.cells))
            row_ref = SemanticRef((*prefix, "rows", row.key or f"@{i}"))
            cells = tuple(
                ReferencedText(
                    SemanticRef((*row_ref.keys, column.reference.keys[-1])),
                    resolve_text(cell, context.localization).nodes,
                )
                for column, cell in zip(columns, row.cells, strict=True)
            )
            rows.append(TableRecord(row_ref, cells))
        preferred = table_forms[0] if table_forms else None
        if value.display == sem.TableDisplay.TABULAR:
            preferred = TableForm.NATIVE
        elif value.display == sem.TableDisplay.RECORDS:
            preferred = TableForm.RECORDS
        elif value.display == sem.TableDisplay.MATRIX:
            preferred = None
        offers: list[RealizationOffer[RenderTargetT, ContextT, Realization]] = []
        for index, form in enumerate(table_forms):
            payload = TableRealization(columns, tuple(rows), form)
            units = tuple(frozenset(cell.reference for cell in row.cells) for row in rows)
            repeated = frozenset(column.reference for column in columns)
            fragments = (
                FragmentContract(units, tuple(range(1, len(rows) + 1)), payload.fragment, repeated) if rows else None
            )
            offers.append(
                RealizationOffer(
                    occurrence,
                    form.value,
                    payload,
                    preference=0 if form == preferred else index + 1,
                    fragments=fragments,
                )
            )
        return tuple(offers)
    if isinstance(value, sem.MediaItem):
        description = (
            "".join(tree.plain(node) for node in resolve_text(value.description, context.localization).nodes)
            if value.description is not None
            else ""
        )
        return (RealizationOffer(occurrence, "image", ImageRealization(value.source, description, value.spoiler)),)
    if isinstance(value, sem.Download):
        payload = DownloadRealization(
            value.asset,
            resolve_text(value.label, context.localization).nodes if value.label is not None else (),
            resolve_text(value.description, context.localization).nodes if value.description is not None else (),
            value.spoiler,
        )
        return (RealizationOffer(occurrence, "download", payload),)
    return ()


def record_text(table: TableRealization) -> tuple[tuple[tree.Node, ...], ...]:
    """Keep each column label beside its value, including schemas with no records."""
    if not table.rows:
        schema: list[tree.Node] = []
        for i, column in enumerate(table.columns):
            if i:
                schema.append(tree.literal(" / "))
            schema.extend(column.nodes)
        return (tuple(schema),)
    result: list[tuple[tree.Node, ...]] = []
    for row in table.rows:
        nodes: list[tree.Node] = []
        for i, (column, cell) in enumerate(zip(table.columns, row.cells, strict=True)):
            if i:
                nodes.append(tree.literal("\n"))
            nodes.extend((*column.nodes, tree.literal(": "), *cell.nodes))
        result.append(tuple(nodes))
    return tuple(result)


def select_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
    *,
    selections: Mapping[OccurrenceId, str],
) -> CandidateComposition[RenderTargetT, ContextT, PayloadT] | UnsupportedCapability:
    """Compose explicit choices without running a fit search or deciding page allocations.

    An occurrence with one offer needs no selection entry. Every choice among
    multiple offers must be explicit until the joint solver takes over selection.
    """
    context = replace(context, palette=occurrence.palette) if occurrence.palette is not None else context
    offers = rules.offers(occurrence, context)
    if isinstance(offers, OfferDiscovery):
        offers = offers.offers
    if not offers:
        return UnsupportedCapability(
            occurrence.identity, type(occurrence.graph.units[occurrence.reference].value).__name__
        )
    choice = selections.get(occurrence.identity)
    if choice is None and len(offers) == 1:
        selected = offers[0]
    else:
        selected = next((offer for offer in offers if offer.key == choice), None)
        if selected is None:
            message = f"an explicit valid representation is required for {occurrence.identity.path!r}"
            raise LayoutInvariantError(message)
    children: list[CandidateComposition[RenderTargetT, ContextT, PayloadT]] = []
    for child in selected.children:
        candidate = select_composition(child, rules, context, selections=selections)
        if isinstance(candidate, UnsupportedCapability):
            return candidate
        children.append(candidate)
    return CandidateComposition(selected, tuple(children))

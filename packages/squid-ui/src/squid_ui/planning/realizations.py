"""Shared lossless realizations for the first composition vocabulary.

Tables retain keyed columns and cells until a target draws the selected form.
This module offers representations; it does not fit or paginate them.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
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
from squid_ui.planning.pagination_content import RootRegion, TextPages
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
    resolve_presentation_text,
)
from squid_ui.presentations import (
    Alternatives,
    Budget,
    Omission,
    Replacement,
    SemanticRef,
    Shortening,
    TextPresentation,
)
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import FIRST_PAGE, Alts, Drop, Never, PageOwnership, Truncate
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.text import TextLike, resolve_text


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
class ProgressRealization(TextRealization):
    value: float = 0
    maximum: float = 1
    label: str | None = None


@dataclass(frozen=True, slots=True)
class FlowRealization:
    pass


@dataclass(frozen=True, slots=True)
class FieldsRealization(FlowRealization):
    pass


@dataclass(frozen=True, slots=True)
class DisclosureRealization(FlowRealization):
    open: bool


@dataclass(frozen=True, slots=True)
class PanelRealization:
    accent: Color | None
    framed: bool = True
    thumbnail: str | None = None
    heading: str = ""
    kind: Literal["block", "section", "article"] = "block"
    spoiler: bool = False


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
class RowRealization:
    pass


@dataclass(frozen=True, slots=True)
class ActionsRealization:
    """Ordered buttons that may occupy multiple target action rows."""

    navigation: bool = False


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
    | DownloadRealization
    | ControlRealization
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
            unit.value, sem.Budgeted | sem.KeepWithNext | sem.Unbreakable | sem.Truncated | Budget | RootRegion
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
) -> (
    tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]
    | OfferDiscovery[RenderTargetT, ContextT, Realization]
):
    context.work.consume()
    value = occurrence.graph.units[occurrence.reference].value
    text_capacity = context.capacities.get(Axis.DISPLAY_TEXT, text_limit)
    keep: Literal["head", "tail"] | None = None
    parent = occurrence.graph.units[occurrence.reference].parent
    inside_panel = False
    while parent is not None:
        ancestor = occurrence.graph.units[parent]
        inside_panel |= isinstance(ancestor.value, sem.Block | sem.Section | sem.Article | primitive.Panel)
        if isinstance(ancestor.value, sem.Truncated):
            keep = ancestor.value.keep
        parent = ancestor.parent
    if inside_panel and panel_text_limit is not None:
        text_capacity = panel_text_limit
    parent = occurrence.graph.units[occurrence.reference].parent
    while parent is not None:
        ancestor = occurrence.graph.units[parent]
        cap = (
            ancestor.value.chars
            if isinstance(ancestor.value, sem.Paged)
            else ancestor.value.preferred
            if isinstance(ancestor.value, sem.Budgeted) and not ancestor.value.stretch
            else ancestor.value.limits.get(Axis.DISPLAY_TEXT)
            if isinstance(ancestor.value, Budget)
            else None
        )
        if cap is not None:
            text_capacity = min(text_capacity, cap) if text_capacity is not None else cap
        parent = ancestor.parent

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
                for retained in range(upper, -1, -1)
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

    if isinstance(value, primitive.Text | primitive.Heading | primitive.Footer | primitive.Code):
        overflow = Truncate(keep) if keep is not None else value.overflow
        if not native_controls or not isinstance(overflow, Never | Truncate | Alts | Drop):
            return ()
        payload = TextRealization(resolve_text(value.content, context.localization).nodes)
        if isinstance(value, primitive.Heading):
            payload = replace(payload, role="heading", level=value.level)
        elif isinstance(value, primitive.Footer):
            payload = replace(payload, role="note")
        elif isinstance(value, primitive.Code):
            payload = replace(payload, role="code", language=value.lang)
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
    if isinstance(value, sem.Budgeted | sem.Paged) and not character_budgets:
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
    if isinstance(value, sem.List | TextPages):
        items = tuple(
            ReferencedText(
                ref,
                resolve_text(item.content, context.localization).nodes,
                f"{index + 1}. "
                if isinstance(value, sem.List) and value.ordered
                else "• "
                if isinstance(value, sem.List)
                else "",
                ordinal=index + 1,
            )
            for index, (ref, item) in enumerate(
                zip(occurrence.graph.children.get(occurrence.reference, ()), value.items, strict=True)
            )
        )
        per = value.per if isinstance(value, TextPages) else value.page_size
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
                        nodes=_slice(item.nodes, start, start + size),
                        prefix=item.prefix if start == 0 else "",
                        continuation=start > 0,
                    )
                    for start in range(0, len(plain), size)
                )
            else:
                expanded.append(item)
            if per is not None and (index + 1) % per == 0:
                required.append(len(expanded))
        items = tuple(expanded)
        payload = (
            TextSequenceRealization(items, list_ordered=value.ordered)
            if isinstance(value, sem.List)
            else TextSequenceRealization(items, value.separator, value.prefix, value.suffix, literal=value.literal)
        )
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
                value.footer if isinstance(value, TextPages) else None,
                value.page,
                value.key,
            ),
            min_fill=value.min_fill if isinstance(value, TextPages) else 0,
            widows=value.widows if isinstance(value, TextPages) else 1,
            owner=value.page,
            cursor_key=value.key,
            required_ends=tuple(required),
        )
        return (RealizationOffer(occurrence, "text-pages", payload, fragments=fragments, region=contract),)
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
        return (RealizationOffer(occurrence, "button", payload, interaction=True, binding=binding, form=form),)
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
                tuple(range(1, length + 1)),
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
        | sem.Fields
        | sem.Media
        | sem.Figure
        | sem.Cluster
        | sem.Themed
        | sem.ActionControls
        | sem.ControlGroup
        | sem.Truncated
        | sem.Region
        | sem.Paged
        | sem.Budgeted
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
            parent = occurrence.graph.units[occurrence.reference].parent
            nested_region = False
            while parent is not None:
                unit = occurrence.graph.units[parent]
                nested_region |= isinstance(unit.value, sem.Region) and not isinstance(unit.value, RootRegion)
                parent = unit.parent
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
            flow=not isinstance(value, sem.Unbreakable | sem.Figure),
            keep_with_next=isinstance(value, sem.KeepWithNext),
            region=region,
        )
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

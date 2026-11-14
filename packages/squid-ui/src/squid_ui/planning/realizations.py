"""Shared lossless realizations for the first composition vocabulary.

Tables retain keyed columns and cells until a target draws the selected form.
This module offers representations; it does not fit or paginate them.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from urllib.parse import quote

from squid_i18n import tree
from squid_ui import scene
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.emoji import Emoji, normalize_emoji
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.planning.composition import (
    CandidateComposition,
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
from squid_ui.planning.pagination_content import RootRegion, TextPages
from squid_ui.planning.resources import Axis
from squid_ui.planning.semantic_adaptation.common import _button_style
from squid_ui.planning.semantic_adaptation.handlers import PresentForm
from squid_ui.planning.semantics import SourceScope, StructuralGroup, UnsupportedSemanticError, check_alternative
from squid_ui.planning.structure import resolve_accent
from squid_ui.planning.text_presentations import ResolvedText, UnsupportedText, _slice, resolve_presentation_text
from squid_ui.presentations import Alternatives, Budget, SemanticRef, TextPresentation
from squid_ui.primitives import nodes as primitive
from squid_ui.primitives.constraints import FIRST_PAGE, PageOwnership
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.text import TextLike, resolve_text


@dataclass(frozen=True, slots=True)
class TextRealization:
    nodes: tuple[tree.Node, ...]
    provenance: ResolvedText | None = None

    def fragment(self, start: int, end: int) -> Realization:
        return replace(self, nodes=_slice(self.nodes, start, end))


@dataclass(frozen=True, slots=True)
class FlowRealization:
    pass


@dataclass(frozen=True, slots=True)
class PanelRealization:
    accent: Color | None


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


@dataclass(frozen=True, slots=True)
class RowRealization:
    pass


@dataclass(frozen=True, slots=True)
class SelectRealization:
    node: scene.Select | scene.RoutedSelect


type Realization = (
    TextSequenceRealization
    | RowRealization
    | SelectRealization
    | TextRealization
    | FlowRealization
    | PanelRealization
    | TableRealization
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
            controls = (
                ()
                if context.navigation is None
                else context.navigation(self.cursor_key, page, pages, context.chrome, self.owner)
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
    parts: list[str] = []
    ref: SemanticRef | None = occurrence.reference
    while ref is not None:
        unit = occurrence.graph.units[ref]
        if unit.parent is not None and not isinstance(
            unit.value, sem.Budgeted | sem.KeepWithNext | sem.Unbreakable | Budget
        ):
            parts.append(quote(ref.keys[-1], safe=""))
        ref = unit.parent
    return "$/" + "/".join(reversed(parts))


def semantic_offers[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
    *,
    table_forms: tuple[TableForm, ...],
    panels: bool,
    disabled_controls: bool = True,
    character_budgets: bool = False,
) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
    context.work.consume()
    value = occurrence.graph.units[occurrence.reference].value
    text_capacity = context.capacities.get(Axis.DISPLAY_TEXT)
    parent = occurrence.graph.units[occurrence.reference].parent
    while parent is not None:
        ancestor = occurrence.graph.units[parent]
        if isinstance(ancestor.value, sem.Paged):
            text_capacity = min(text_capacity, ancestor.value.chars) if text_capacity else ancestor.value.chars
        parent = ancestor.parent
    if isinstance(value, sem.Budgeted | sem.Paged) and not character_budgets:
        return ()
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
    if isinstance(value, sem.ActionControl | sem.RoutedActionControl | sem.Link | sem.FormTrigger):
        # Wire IDs follow semantic ownership, independently of presentation keys.
        encoded = json.dumps(sorted(ref.keys for ref in occurrence.sources), ensure_ascii=True)
        key = "composition." + sha256(encoded.encode()).hexdigest()[:32]
        label = "".join(tree.plain(node) for node in resolve_text(value.label, context.localization).nodes)
        binding = None
        form = None
        match value:
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
    if isinstance(value, sem.Paragraph):
        payload = TextRealization(resolve_text(value.content, context.localization).nodes)
        length = sum(len(tree.plain(node)) for node in payload.nodes)
        fragments = None
        if text_capacity is not None and length > text_capacity:
            context.work.consume(length)
            fragments = FragmentContract(
                (frozenset({occurrence.reference}),) * length,
                tuple(range(1, length + 1)),
                payload.fragment,
                continuations=True,
            )
        return (RealizationOffer(occurrence, "text", payload, fragments=fragments),)
    if isinstance(value, sem.Block) and not panels:
        return ()
    if isinstance(
        value,
        sem.Group
        | sem.Stack
        | sem.ActionControls
        | sem.ControlGroup
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
                if isinstance(value, sem.Region) and ref.keys[-1] == "@header"
                else Placement.FOOTER
                if isinstance(value, sem.Region) and ref.keys[-1] == "@footer"
                else placement,
                occurrence.origin,
            )
            for ref in refs
        )
        payload = (
            PanelRealization(resolve_accent(value.accent, context.palette))
            if isinstance(value, sem.Block) and panels
            else FlowRealization()
        )
        region = None
        if isinstance(value, sem.Region | sem.Paged):
            parent = occurrence.graph.units[occurrence.reference].parent
            nested_region = False
            while parent is not None:
                unit = occurrence.graph.units[parent]
                nested_region |= isinstance(unit.value, sem.Region)
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
        return (
            RealizationOffer(
                occurrence,
                "container",
                payload,
                children,
                flow=not isinstance(value, sem.Unbreakable),
                keep_with_next=isinstance(value, sem.KeepWithNext),
                region=region,
            ),
        )
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

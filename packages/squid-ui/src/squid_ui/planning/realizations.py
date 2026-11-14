"""Shared lossless realizations for the first composition vocabulary.

Tables retain keyed columns and cells until a target draws the selected form.
This module offers representations; it does not fit or paginate them.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256

from squid_i18n import tree
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.capabilities import Capability
from squid_ui.errors import LayoutInvariantError
from squid_ui.forms import FormBinding
from squid_ui.interactions import ActionBinding, ActionMode
from squid_ui.planning.composition import (
    CandidateComposition,
    FragmentContract,
    FragmentCoverage,
    OccurrenceId,
    Placement,
    PlanningContext,
    RealizationOffer,
    RegionContract,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.semantic_adaptation.common import _button_style
from squid_ui.planning.semantic_adaptation.handlers import PresentForm
from squid_ui.planning.semantics import SourceScope, StructuralGroup, check_alternative
from squid_ui.planning.structure import resolve_accent
from squid_ui.planning.text_presentations import ResolvedText, UnsupportedText, resolve_presentation_text
from squid_ui.presentations import Alternatives, Budget, SemanticRef, TextPresentation
from squid_ui.primitives.styles import ActionStyle, Color
from squid_ui.text import TextLike, resolve_text


@dataclass(frozen=True, slots=True)
class TextRealization:
    nodes: tuple[tree.Node, ...]
    provenance: ResolvedText | None = None


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


type Realization = (
    TextRealization | FlowRealization | PanelRealization | TableRealization | DownloadRealization | ControlRealization
)


@dataclass(frozen=True, slots=True)
class RegionNavigation[RenderTargetT, ContextT]:
    occurrence: SemanticOccurrence[RenderTargetT, ContextT]
    disabled_controls: bool
    footer: Callable[[int, int], TextLike] | None = None

    def __call__(
        self, page: int, pages: int, context: PlanningContext
    ) -> tuple[CandidateComposition[RenderTargetT, ContextT, Realization], ...]:
        if pages == 1:
            return ()
        occurrence = self.occurrence
        route = "composition.page." + sha256("/".join(occurrence.identity.path).encode()).hexdigest()[:24]
        payloads: list[Realization] = []
        if page > 0 or self.disabled_controls:
            payloads.append(ControlRealization("Previous", route=f"{route}.{max(0, page - 1)}", disabled=page == 0))
        label = self.footer(page + 1, pages) if self.footer is not None else f"{page + 1} / {pages}"
        payloads.append(TextRealization(resolve_text(label, context.localization).nodes))
        if page + 1 < pages or self.disabled_controls:
            payloads.append(
                ControlRealization("Next", route=f"{route}.{min(pages - 1, page + 1)}", disabled=page + 1 == pages)
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
            result.append(CandidateComposition(RealizationOffer(placed, "navigation", payload)))
        return tuple(result)


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
        return (
            RealizationOffer(
                occurrence, "text", TextRealization(resolve_text(value.content, context.localization).nodes)
            ),
        )
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
        refs = [ref for ref, unit in occurrence.graph.units.items() if unit.parent == occurrence.reference]
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
            region = RegionContract(
                value.key,
                RegionNavigation(occurrence, disabled_controls, value.footer if isinstance(value, sem.Paged) else None),
                value.chars if isinstance(value, sem.Paged) else None,
                value.min_fill if isinstance(value, sem.Paged) else 0,
                value.widows if isinstance(value, sem.Paged) else 1,
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
            context.work.consume()
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
            if occurrence.origin is not None:
                units = tuple(
                    frozenset(source for ref in unit for source in occurrence.origin.references(ref)) for unit in units
                )
            repeated = frozenset(column.reference for column in columns)
            if occurrence.origin is not None:
                repeated = frozenset(source for ref in repeated for source in occurrence.origin.references(ref))
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

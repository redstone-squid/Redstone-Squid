"""Shared lossless realizations for the first composition vocabulary.

Tables retain keyed columns and cells until a target draws the selected form.
This module offers representations; it does not fit or paginate them.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from squid_i18n import tree
from squid_ui import semantic as sem
from squid_ui.assets import Asset
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    OccurrenceId,
    Placement,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.semantics import StructuralGroup
from squid_ui.planning.structure import resolve_accent
from squid_ui.presentations import SemanticRef
from squid_ui.primitives.styles import Color
from squid_ui.text import resolve_text


@dataclass(frozen=True, slots=True)
class TextRealization:
    nodes: tuple[tree.Node, ...]


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


@dataclass(frozen=True, slots=True)
class DownloadRealization:
    asset: Asset
    label: tuple[tree.Node, ...]
    description: tuple[tree.Node, ...]
    spoiler: bool


type Realization = TextRealization | FlowRealization | PanelRealization | TableRealization | DownloadRealization


def semantic_offers[RenderTargetT, ContextT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    context: PlanningContext,
    *,
    table_forms: tuple[TableForm, ...],
    panels: bool,
) -> tuple[RealizationOffer[RenderTargetT, ContextT, Realization], ...]:
    context.work.consume()
    value = occurrence.graph.units[occurrence.reference].value
    if isinstance(value, sem.Paragraph):
        return (
            RealizationOffer(
                occurrence, "text", TextRealization(resolve_text(value.content, context.localization).nodes)
            ),
        )
    if isinstance(value, sem.Block) and not panels:
        return ()
    if isinstance(value, sem.Group | sem.Stack | sem.Region | StructuralGroup | sem.Block):
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
                OccurrenceId((*occurrence.identity.path, ref.keys[-1])), occurrence.graph, ref, placement
            )
            for ref in refs
        )
        payload = (
            PanelRealization(resolve_accent(value.accent, context.palette))
            if isinstance(value, sem.Block) and panels
            else FlowRealization()
        )
        return (RealizationOffer(occurrence, "container", payload, children),)
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
        allowed = (
            (TableForm.NATIVE,)
            if value.display == sem.TableDisplay.TABULAR
            else (TableForm.RECORDS,)
            if value.display == sem.TableDisplay.RECORDS
            else table_forms
            if value.display == sem.TableDisplay.AUTO
            else ()
        )
        return tuple(
            RealizationOffer(occurrence, form.value, TableRealization(columns, tuple(rows), form), preference=index)
            for index, form in enumerate(table_forms)
            if form in allowed
        )
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


def select_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, EvidenceT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, EvidenceT],
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

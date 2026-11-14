"""Retain semantic units and correspondence independently of target representations."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from squid_ui import semantic as sem
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.interaction_semantics import Interaction, interaction_changes
from squid_ui.presentations import (
    Alternative,
    Alternatives,
    InteractionChange,
    Omission,
    SemanticRef,
    Shortening,
    TextPresentation,
)
from squid_ui.target_types import Renderable
from squid_ui.text import TextLike


@dataclass(frozen=True, slots=True)
class TableCell:
    row: str
    column: str
    content: TextLike


@dataclass(frozen=True, slots=True)
class FieldPart:
    part: Literal["label", "value"]
    content: TextLike


@dataclass(frozen=True, slots=True)
class StructuralGroup:
    part: Literal["columns", "rows", "header", "footer"]


type EmbeddedValue[ContextT] = (
    sem.Column | sem.TableRow | sem.Field | sem.ListItem | sem.ControlGroup[ContextT] | Interaction[ContextT]
)
type SemanticValue[RenderTargetT, ContextT] = (
    sem.SemanticNode[RenderTargetT, ContextT]
    | sem.Adaptation[RenderTargetT, ContextT]
    | EmbeddedValue[ContextT]
    | TableCell
    | FieldPart
    | TextPresentation
    | StructuralGroup
)


@dataclass(frozen=True, slots=True)
class SemanticUnit[RenderTargetT, ContextT]:
    reference: SemanticRef
    value: SemanticValue[RenderTargetT, ContextT]
    parent: SemanticRef | None
    atomic: bool


@dataclass(frozen=True, slots=True)
class SemanticGraph[RenderTargetT, ContextT]:
    units: Mapping[SemanticRef, SemanticUnit[RenderTargetT, ContextT]]

    def resolve(self, reference: SemanticRef) -> SemanticRef:
        if reference in self.units:
            return reference
        matches = [key for key in self.units if key.keys[-len(reference.keys) :] == reference.keys]
        if len(matches) != 1:
            message = f"semantic reference {reference.keys!r} is {'ambiguous' if matches else 'unknown'}"
            raise LayoutInvariantError(message)
        return matches[0]

    def atoms(self, reference: SemanticRef) -> frozenset[SemanticRef]:
        root = self.resolve(reference)
        return frozenset(
            key for key, unit in self.units.items() if unit.atomic and key.keys[: len(root.keys)] == root.keys
        )

    @property
    def leaves(self) -> frozenset[SemanticRef]:
        return frozenset(key for key, unit in self.units.items() if unit.atomic)


def _key[RenderTargetT, ContextT](value: Renderable[RenderTargetT, ContextT] | EmbeddedValue[ContextT]) -> str | None:
    match value:
        case (
            sem.Group()
            | sem.Stack()
            | sem.Block()
            | sem.Section()
            | sem.Paragraph()
            | sem.Table()
            | sem.List()
            | sem.ListItem()
            | sem.Column()
            | sem.TableRow()
            | sem.Field()
            | sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.ControlGroup()
            | sem.ActionControls()
            | sem.FormTrigger()
            | sem.Choices()
            | sem.Entities()
            | sem.Toggle()
            | sem.RoutedChoices()
            | sem.Region()
            | sem.Paged()
            | sem.Download()
            | TextPresentation()
        ):
            return value.key
        case _:
            return None


def semantic_graph[RenderTargetT, ContextT](
    node: Renderable[RenderTargetT, ContextT],
) -> SemanticGraph[RenderTargetT, ContextT]:
    """Index supported structure without lowering it or invoking application callbacks.

    Keys are scoped by their containers. A short reference is accepted only when
    unambiguous. Positional identities do not promise stability under reordering.
    Unsupported nodes are rejected rather than guessed atomic from their shape.
    """
    units: dict[SemanticRef, SemanticUnit[RenderTargetT, ContextT]] = {}

    def add(
        value: SemanticValue[RenderTargetT, ContextT],
        path: tuple[str, ...],
        parent: SemanticRef | None,
        *,
        atomic: bool,
    ) -> SemanticRef:
        reference = SemanticRef(path)
        if reference in units:
            message = f"duplicate semantic identity {path!r}"
            raise LayoutInvariantError(message)
        units[reference] = SemanticUnit(reference, value, parent, atomic)
        return reference

    def visit(
        value: Renderable[RenderTargetT, ContextT] | EmbeddedValue[ContextT],
        parent: SemanticRef | None,
        index: int,
        *,
        name: str | None = None,
    ) -> None:
        if isinstance(value, Alternatives):
            visit(value.content, parent, index, name=name)
            return
        key = name or _key(value) or f"@{index}"
        path = (*(() if parent is None else parent.keys), key)
        match value:
            case sem.Unbreakable() | sem.KeepWithNext() | sem.Budgeted() | sem.Paged():
                root = add(value, path, parent, atomic=False)
                visit(value.node, root, 0)
            case sem.Table():
                root = add(value, path, parent, atomic=False)
                columns = add(StructuralGroup("columns"), (*path, "columns"), root, atomic=False)
                for i, column in enumerate(value.columns.columns):
                    visit(column, columns, i)
                rows = add(StructuralGroup("rows"), (*path, "rows"), root, atomic=False)
                for i, row in enumerate(value.rows):
                    row_key = row.key or f"@{i}"
                    row_ref = add(row, (*rows.keys, row_key), rows, atomic=False)
                    for j, (column, cell) in enumerate(zip(value.columns.columns, row.cells, strict=True)):
                        column_key = column.key or f"@{j}"
                        add(TableCell(row_key, column_key, cell), (*row_ref.keys, column_key), row_ref, atomic=True)
            case sem.Fields():
                root = add(value, path, parent, atomic=False)
                for i, entry in enumerate(value.fields):
                    visit(entry, root, i)
            case sem.Field():
                root = add(value, path, parent, atomic=False)
                add(FieldPart("label", value.label), (*path, "label"), root, atomic=True)
                add(FieldPart("value", value.value), (*path, "value"), root, atomic=True)
            case sem.List():
                root = add(value, path, parent, atomic=False)
                for i, item in enumerate(value.items):
                    visit(item, root, i)
            case sem.ActionControls():
                root = add(value, path, parent, atomic=False)
                for i, control in enumerate(value.items):
                    visit(control, root, i)
            case sem.ControlGroup():
                root = add(value, path, parent, atomic=False)
                if value.label is not None:
                    add(sem.Paragraph(value.label), (*path, "@label"), root, atomic=True)
                for i, control in enumerate(value.controls):
                    visit(control, root, i)
            case (
                sem.Group()
                | sem.Stack()
                | sem.Cluster()
                | sem.Themed()
                | sem.Block()
                | sem.Section()
                | sem.Article()
                | sem.Aside()
                | sem.Region()
            ):
                root = add(value, path, parent, atomic=False)
                if isinstance(value, sem.Section | sem.Article):
                    visit(value.heading, root, 0, name="@heading")
                for i, child in enumerate(value.children):
                    visit(child, root, i)
                if isinstance(value, sem.Region):
                    for part, children in (
                        (StructuralGroup("header"), value.header),
                        (StructuralGroup("footer"), value.footer),
                    ):
                        slot = add(part, (*path, f"@{part.part}"), root, atomic=False)
                        for i, child in enumerate(children):
                            visit(child, slot, i)
            case (
                sem.Paragraph()
                | sem.Heading()
                | sem.Note()
                | sem.Code()
                | sem.Quote()
                | sem.Download()
                | TextPresentation()
                | sem.Column()
                | sem.ListItem()
                | sem.ActionControl()
                | sem.RoutedActionControl()
                | sem.Link()
                | sem.Toggle()
                | sem.FormTrigger()
                | sem.Choices()
                | sem.Entities()
                | sem.RoutedChoices()
            ):
                add(value, path, parent, atomic=True)
            case _:
                message = f"semantic correspondence does not yet support {type(value).__name__}"
                raise LayoutInvariantError(message)

    visit(node, None, 0)
    return SemanticGraph(MappingProxyType(units))


def interaction[RenderTargetT, ContextT](value: SemanticValue[RenderTargetT, ContextT]) -> Interaction[ContextT] | None:
    match value:
        case (
            sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.Toggle()
            | sem.FormTrigger()
            | sem.Choices()
            | sem.Entities()
            | sem.RoutedChoices()
        ):
            return value
        case _:
            return None


@dataclass(frozen=True, slots=True)
class CheckedAlternative[RenderTargetT, ContextT]:
    choice: Alternative[RenderTargetT, ContextT]
    source: SemanticGraph[RenderTargetT, ContextT]
    presentation: SemanticGraph[RenderTargetT, ContextT]
    coverage: Mapping[SemanticRef, frozenset[SemanticRef]]
    omitted: frozenset[SemanticRef]
    asserted: frozenset[SemanticRef]
    """Presentation units whose textual equivalence depends on the author's claim."""


def check_alternative[RenderTargetT, ContextT](
    group: Alternatives[RenderTargetT, ContextT], choice: Alternative[RenderTargetT, ContextT]
) -> CheckedAlternative[RenderTargetT, ContextT]:
    """Check references, coverage, and declared behavior changes, without proving prose meaning.

    This validates structural declarations only. Target evaluation must also check
    shortening bounds after localization and enforce inherited layout requirements.
    """
    source, presentation = semantic_graph(group.content), semantic_graph(choice.node)
    explicit: dict[SemanticRef, frozenset[SemanticRef]] = {}
    for mapping in choice.correspondence:
        target = presentation.resolve(mapping.presentation)
        if target in explicit:
            msg = f"duplicate correspondence for {target.keys!r}"
            raise LayoutInvariantError(msg)
        explicit[target] = frozenset(unit for item in mapping.content for unit in source.atoms(item))

    omitted: set[SemanticRef] = set()
    changed: dict[SemanticRef, set[str]] = {}
    shortenings: dict[SemanticRef, Shortening] = {}
    for effect in choice.fidelity.effects:
        affected = source.atoms(effect.source)
        if isinstance(effect, Omission):
            omitted.update(affected)
        elif isinstance(effect, InteractionChange):
            for unit in affected:
                if effect.attribute not in interaction_changes(interaction(source.units[unit].value), None):
                    msg = f"unknown interaction attribute {effect.attribute!r}"
                    raise LayoutInvariantError(msg)
                changed.setdefault(unit, set()).add(effect.attribute)
        elif isinstance(effect, Shortening):
            unit = source.resolve(effect.source)
            source_text(source.units[unit].value)
            if unit in shortenings:
                message = f"duplicate shortening declaration at {unit.keys!r}"
                raise LayoutInvariantError(message)
            shortenings[unit] = effect

    coverage: dict[SemanticRef, frozenset[SemanticRef]] = {}
    asserted: set[SemanticRef] = set()
    applied_shortening: set[SemanticRef] = set()
    for target in presentation.leaves:
        owners = [item for item in explicit if target.keys[: len(item.keys)] == item.keys]
        target_value = presentation.units[target].value
        if isinstance(target_value, TextPresentation):
            if owners:
                message = "text fragments carry their own correspondence"
                raise LayoutInvariantError(message)
            covered_units: set[SemanticRef] = set()
            for part in target_value.parts:
                if not isinstance(part, SemanticRef | Shortening):
                    continue
                source_ref = source.resolve(part.source if isinstance(part, Shortening) else part)
                source_text(source.units[source_ref].value)
                covered_units.add(source_ref)
                if isinstance(part, Shortening):
                    declared = shortenings.get(source_ref)
                    if declared is None or (declared.max_chars, declared.keep) != (part.max_chars, part.keep):
                        message = f"undeclared shortening operation at {source_ref.keys!r}"
                        raise LayoutInvariantError(message)
                    applied_shortening.add(source_ref)
            covered = frozenset(covered_units)
        elif owners:
            asserted.add(target)
            selected = max(owners, key=lambda item: len(item.keys))
            covered = explicit[selected]
        else:
            # Match named units across different presentation containers. An
            # unkeyed unit can only match the same positional path and kind.
            matches = [
                item
                for item in source.leaves
                if (item == target or (not target.keys[-1].startswith("@") and item.keys[-1] == target.keys[-1]))
                and type(source.units[item].value) is type(presentation.units[target].value)
            ]
            if target in matches:
                matches = [target]
            if len(matches) != 1:
                msg = f"presentation unit {target.keys!r} needs an explicit correspondence"
                raise LayoutInvariantError(msg)
            covered = frozenset(matches)
            if source.units[matches[0]].value != target_value:
                asserted.add(target)
        coverage[target] = covered
        target_behavior = interaction(presentation.units[target].value)
        for unit in covered:
            changed_attributes = interaction_changes(interaction(source.units[unit].value), target_behavior)
            if changed_attributes - changed.get(unit, set()):
                msg = f"undeclared interaction change at {unit.keys!r}"
                raise LayoutInvariantError(msg)

    if applied_shortening != shortenings.keys():
        message = "shortening declarations require source-referenced text fragments"
        raise LayoutInvariantError(message)
    retained = frozenset(unit for covered in coverage.values() for unit in covered)
    missing = source.leaves - retained
    if missing != omitted:
        msg = f"declared omissions do not match missing semantic units: {missing ^ omitted}"
        raise LayoutInvariantError(msg)
    return CheckedAlternative(
        choice, source, presentation, MappingProxyType(coverage), frozenset(omitted), frozenset(asserted)
    )


def source_text[RenderTargetT, ContextT](value: SemanticValue[RenderTargetT, ContextT]) -> TextLike:
    """Return one source text unit; containers need references to their individual parts."""
    match value:
        case sem.Paragraph() | sem.Heading() | sem.Note() | sem.Code() | sem.ListItem() | TableCell() | FieldPart():
            return value.content
        case sem.Column():
            return value.heading
        case _:
            message = f"a text fragment must reference one text unit, not {type(value).__name__}"
            raise LayoutInvariantError(message)

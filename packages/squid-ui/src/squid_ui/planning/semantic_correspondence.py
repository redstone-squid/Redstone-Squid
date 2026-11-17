"""Check presentation correspondence and preserved layout boundaries."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from squid_ui.authoring import semantic as sem
from squid_ui.authoring.presentations import (
    Alternative,
    Alternatives,
    Budget,
    InteractionChange,
    Omission,
    Replacement,
    SemanticRef,
    Shortening,
    TextPresentation,
)
from squid_ui.content.text import TextLike
from squid_ui.errors import LayoutInvariantError
from squid_ui.native import NativeTraits, NativeValue
from squid_ui.planning.interaction_semantics import (
    DisclosureToggle,
    Interaction,
    ItemBack,
    ItemSummary,
    NavigationChoice,
    interaction_changes,
)
from squid_ui.planning.semantic_index import (
    FieldPart,
    RosterSection,
    SemanticGraph,
    SemanticValue,
    TableCell,
    _key,
    semantic_graph,
)


def interaction[RenderTargetT, ContextT](value: SemanticValue[RenderTargetT, ContextT]) -> Interaction[ContextT] | None:
    if isinstance(value, NativeValue):
        return value if NativeTraits.of(value).interaction is not None else None
    match value:
        case (
            sem.ActionControl()
            | sem.RoutedActionControl()
            | sem.Link()
            | sem.Toggle()
            | sem.FormTrigger()
            | sem.Choices()
            | sem.Entities()
            | NavigationChoice()
            | DisclosureToggle()
            | ItemSummary()
            | ItemBack()
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
    group: Alternatives[RenderTargetT, ContextT],
    choice: Alternative[RenderTargetT, ContextT],
    *,
    consume: Callable[[], None] | None = None,
) -> CheckedAlternative[RenderTargetT, ContextT]:
    """Check references, coverage, and declared behavior changes, without proving prose meaning.

    This validates structural declarations only. Target evaluation must also check
    shortening bounds after localization and enforce inherited layout requirements.
    """
    source, presentation = semantic_graph(group.content, consume=consume), semantic_graph(choice.node, consume=consume)
    explicit: dict[SemanticRef, frozenset[SemanticRef]] = {}
    for mapping in choice.correspondence:
        target = presentation.resolve(mapping.presentation)
        if target in explicit:
            msg = f"duplicate correspondence for {target.keys!r}"
            raise LayoutInvariantError(msg)
        explicit[target] = frozenset(unit for item in mapping.content for unit in source.atoms(item))

    omitted: set[SemanticRef] = set()
    replaced: set[SemanticRef] = set()
    changed: dict[SemanticRef, set[str]] = {}
    shortenings: dict[SemanticRef, Shortening] = {}
    for effect in choice.fidelity.effects:
        affected = source.atoms(effect.source)
        if isinstance(effect, Omission):
            omitted.update(affected)
        elif isinstance(effect, Replacement):
            replaced.update(affected)
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
        if consume is not None:
            consume()
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
            if unit in replaced:
                continue
            changed_attributes = interaction_changes(interaction(source.units[unit].value), target_behavior)
            if changed_attributes - changed.get(unit, set()):
                msg = f"undeclared interaction change at {unit.keys!r}"
                raise LayoutInvariantError(msg)

    if applied_shortening != set(shortenings):
        message = "shortening declarations require source-referenced text fragments"
        raise LayoutInvariantError(message)
    retained = frozenset(unit for covered in coverage.values() for unit in covered)
    missing = source.leaves - retained
    if missing != frozenset(omitted | (replaced - retained)):
        msg = f"declared omissions do not match missing semantic units: {missing ^ omitted}"
        raise LayoutInvariantError(msg)
    checked = CheckedAlternative(
        choice, source, presentation, MappingProxyType(coverage), frozenset(omitted), frozenset(asserted)
    )
    _check_boundaries(checked)
    return checked


def source_text[R, C](value: SemanticValue[R, C]) -> TextLike:
    """Return one source text unit; containers require references to their parts."""
    if isinstance(value, NativeValue) and (text := NativeTraits.of(value).text) is not None:
        return text
    match value:
        case sem.Paragraph() | sem.Heading() | sem.Note() | sem.Code() | sem.ListItem() | TableCell() | FieldPart():
            return value.content
        case sem.Column():
            return value.heading
        case _:
            message = f"a text fragment must reference one text unit, not {type(value).__name__}"
            raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class SourceScope[RenderTargetT, ContextT]:
    """Translate one presentation's references back through enclosing alternatives."""

    checked: CheckedAlternative[RenderTargetT, ContextT]
    group: SemanticRef
    parent: SourceScope[RenderTargetT, ContextT] | None = None

    def source_references(self, reference: SemanticRef) -> frozenset[SemanticRef]:
        scoped = SemanticRef((*self.group.keys, *reference.keys))
        return self.parent.references(scoped) if self.parent is not None else frozenset({scoped})

    def references(self, presentation: SemanticRef) -> frozenset[SemanticRef]:
        sources = {
            source
            for target, covered in self.checked.coverage.items()
            if target.keys[: len(presentation.keys)] == presentation.keys
            for source in covered
        }
        return frozenset(result for source in sources for result in self.source_references(source))

    def page_boundary(self, presentation: SemanticRef) -> SemanticRef:
        """Locate a preserved pager in the authored graph, across nested alternatives."""
        value = self.checked.presentation.units[presentation].value
        if not isinstance(value, sem.Region | sem.Paged | sem.List | sem.Navigation | sem.Items | sem.Grid) and not (
            NativeTraits.of(value).pagination is not None
        ):
            message = "page boundary resolution needs a pagination scope"
            raise LayoutInvariantError(message)
        covered = frozenset(
            source
            for target, sources in self.checked.coverage.items()
            if target.keys[: len(presentation.keys)] == presentation.keys
            for source in sources
        )
        matches = [
            reference
            for reference, unit in self.checked.source.units.items()
            if type(unit.value) is type(value)
            and _key(unit.value) == _key(value)
            and covered <= self.checked.source.atoms(reference)
        ]
        if len(matches) != 1:
            message = "an alternate pager needs one unambiguous authored boundary"
            raise LayoutInvariantError(message)
        scoped = SemanticRef((*self.group.keys, *matches[0].keys))
        return self.parent.page_boundary(scoped) if self.parent is not None else scoped


def budget_floor[RenderTargetT, ContextT](
    graph: SemanticGraph[RenderTargetT, ContextT],
    reference: SemanticRef,
    inherited: Iterable[SemanticValue[RenderTargetT, ContextT]] = (),
) -> int:
    """Return the hard character reservation after explicit best-effort permission."""
    unit = graph.units[reference]
    native_budget = NativeTraits.of(unit.value).budget
    if native_budget is not None:
        if native_budget.best_effort:
            return 0
    elif not isinstance(unit.value, sem.Budgeted) or isinstance(unit.value.node, sem.BestEffort):
        return 0
    if any(isinstance(value, sem.BestEffort) for value in inherited):
        return 0
    parent = unit.parent
    while parent is not None:
        ancestor = graph.units[parent]
        if isinstance(ancestor.value, sem.BestEffort):
            return 0
        parent = ancestor.parent
    return (
        native_budget.minimum
        if native_budget is not None
        else unit.value.minimum
        if isinstance(unit.value, sem.Budgeted)
        else 0
    )


def _check_boundaries[RenderTargetT, ContextT](checked: CheckedAlternative[RenderTargetT, ContextT]) -> None:
    """Preserve hard layout scopes even when presentations rearrange their contents."""
    for reference, unit in checked.source.units.items():
        constraint = unit.value
        if (
            not isinstance(
                constraint,
                Budget
                | sem.Budgeted
                | sem.Unbreakable
                | sem.KeepWithNext
                | sem.Paged
                | sem.Region
                | sem.List
                | sem.Navigation
                | sem.Items
                | sem.Grid
                | RosterSection,
            )
            and NativeTraits.of(constraint).boundary is None
        ):
            continue
        inside = checked.source.atoms(reference)
        targets = frozenset(target for target, covered in checked.coverage.items() if covered & inside)
        if not targets:
            continue
        if any(checked.coverage[target] - inside for target in targets):
            message = f"alternative merges content across a {type(constraint).__name__} boundary at {reference.keys!r}"
            raise LayoutInvariantError(message)
        matches = [
            (target, other.value)
            for target, other in checked.presentation.units.items()
            if type(other.value) is type(constraint) and checked.presentation.atoms(target) == targets
        ]
        preserved = False
        for target, other in matches:
            # Avoid expanding every pair of generic node types before matching concrete cases.
            match cast(object, constraint), cast(object, other):
                case Budget() as constraint, Budget() as other:
                    preserved = all(
                        axis in other.limits and other.limits[axis] <= cap for axis, cap in constraint.limits.items()
                    )
                case sem.Budgeted() as constraint, sem.Budgeted() as other:
                    preserved = (constraint.minimum, constraint.preferred, constraint.stretch) == (
                        other.minimum,
                        other.preferred,
                        other.stretch,
                    ) and budget_floor(checked.presentation, target) >= budget_floor(checked.source, reference)
                case sem.Paged() as constraint, sem.Paged() as other:
                    preserved = (
                        constraint.key,
                        constraint.page,
                        constraint.chars,
                        constraint.min_fill,
                        constraint.widows,
                        constraint.footer,
                    ) == (other.key, other.page, other.chars, other.min_fill, other.widows, other.footer)
                case sem.Region() as constraint, sem.Region() as other:
                    preserved = (constraint.key, constraint.page, constraint.header, constraint.footer) == (
                        other.key,
                        other.page,
                        other.header,
                        other.footer,
                    )
                case sem.Items() as constraint, sem.Items() as other:
                    preserved = (constraint.key, constraint.opened) == (other.key, other.opened)
                case sem.Navigation() as constraint, sem.Navigation() as other:
                    preserved = (constraint.key, constraint.current) == (other.key, other.current)
                case sem.Grid() as constraint, sem.Grid() as other:
                    preserved = (constraint.key, constraint.columns, tuple(cell.key for cell in constraint.cells)) == (
                        other.key,
                        other.columns,
                        tuple(cell.key for cell in other.cells),
                    )
                case sem.List() as constraint, sem.List() as other:
                    preserved = (constraint.key, constraint.page, constraint.page_size) == (
                        other.key,
                        other.page,
                        other.page_size,
                    )
                case (sem.Unbreakable() as constraint, sem.Unbreakable() as other) | (
                    sem.KeepWithNext() as constraint,
                    sem.KeepWithNext() as other,
                ):
                    preserved = True
                case RosterSection() as constraint, RosterSection() as other:
                    preserved = constraint == other
            if isinstance(constraint, NativeValue) and isinstance(other, NativeValue):
                before, after = NativeTraits.of(constraint), NativeTraits.of(other)
                preserved = (
                    before.boundary == after.boundary
                    and (not before.unbreakable or after.unbreakable)
                    and (not before.keep_with_next or after.keep_with_next)
                )
                if before.budget is not None:
                    preserved &= budget_floor(checked.presentation, target) >= budget_floor(checked.source, reference)
            if preserved:
                break
        if not preserved:
            message = f"alternative must preserve the {type(constraint).__name__} boundary at {reference.keys!r}"
            raise LayoutInvariantError(message)

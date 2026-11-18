"""Check every reachable page of one proposed allocation before it can be selected.

A family is feasible only when every visible page combination passes target
evaluation and allocation constraints. Construction and finalization happen
inside the work allowance, so a retained family already has all its outputs.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from squid_ui.authoring.presentations import Budget
from squid_ui.authoring.semantic import Budgeted
from squid_ui.interactions.ownership import Controlled
from squid_ui.layout import FactUnavailableError
from squid_ui.native import NativeTraits
from squid_ui.overflow import RoutedPage, resolve_page
from squid_ui.planning.allocation import CertifiedPage, PageFamily
from squid_ui.planning.allocation_policy import RegionPolicy
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    ConstraintViolation,
    Evaluation,
    OccurrenceId,
    Placement,
    PlanningContext,
    SharedResourceConflictError,
    TargetRules,
    UnsupportedCapability,
    construct_composition,
    evaluate_composition,
)
from squid_ui.planning.fragments import FragmentTree
from squid_ui.planning.layout_snapshot import FamilyLayout
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.reveals import RevealResolver
from squid_ui.planning.semantic_correspondence import budget_floor


@dataclass(frozen=True, slots=True)
class FamilyCertificate[OutputT]:
    """Measurements and built outputs for a complete feasible page family."""

    peaks: Mapping[OccurrenceId, ResourceCost]
    break_shortfalls: Mapping[tuple[OccurrenceId, int], int]
    pages: tuple[CertifiedPage[OutputT], ...]


@dataclass(frozen=True, slots=True)
class FamilyRejection:
    """The first visible page that failed certification."""

    violations: tuple[CandidateViolation, ...]


def _allocation_checks[RenderTargetT, ContextT, PayloadT, ResourceT, PreparationT](
    visible: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    evaluation: Evaluation[ResourceT, PreparationT],
    family: PageFamily[RenderTargetT, ContextT, PayloadT],
    regions: Mapping[OccurrenceId, FragmentTree[RenderTargetT, ContextT, PayloadT]],
    policies: Mapping[OccurrenceId, RegionPolicy],
    context: PlanningContext,
) -> tuple[tuple[CandidateViolation, ...], dict[OccurrenceId, ResourceCost], dict[tuple[OccurrenceId, int], int]]:
    violations: list[CandidateViolation] = []
    costs: dict[OccurrenceId, ResourceCost] = {}
    break_shortfalls: dict[tuple[OccurrenceId, int], int] = {}
    navigation_chars: dict[OccurrenceId, int] = {}

    def visit(part: CandidateComposition[RenderTargetT, ContextT, PayloadT]) -> ResourceCost:
        context.work.consume()
        extra = EMPTY_COST
        for child in part.children:
            extra += visit(child)
        occurrence = part.offer.occurrence
        coverage = occurrence.fragment
        identity = coverage.logical if coverage is not None else occurrence.identity
        cost = evaluation.scoped_costs.get(occurrence.identity, EMPTY_COST)
        navigation_chars[occurrence.identity] = (
            cost.get(Axis.DISPLAY_TEXT)
            if occurrence.placement == Placement.NAVIGATION
            else sum(navigation_chars[child.offer.occurrence.identity] for child in part.children)
        )
        if identity in regions and coverage is not None and occurrence.placement != Placement.NAVIGATION:
            node = regions[identity]
            contract = node.candidate.offer.region
            assert contract is not None
            # A character grant covers content and authored chrome. Navigation has
            # its own exact cost in the enclosing composition.
            navigation = sum(
                evaluation.scoped_costs.get(child.offer.occurrence.identity, EMPTY_COST).get(Axis.DISPLAY_TEXT)
                for child in part.children
                if child.offer.occurrence.placement == Placement.NAVIGATION
            )
            body_cost = ResourceCost(
                {**cost.values, Axis.DISPLAY_TEXT: max(0, cost.get(Axis.DISPLAY_TEXT) - navigation)}
            )
            costs[identity] = body_cost
            if contract.chars is not None:
                if Axis.DISPLAY_TEXT not in evaluation.resource_axes:
                    violations.append(UnsupportedCapability(identity, "character page limit"))
                elif body_cost.get(Axis.DISPLAY_TEXT) > contract.chars:
                    violations.append(
                        CapacityExceeded(identity, Axis.DISPLAY_TEXT, body_cost.get(Axis.DISPLAY_TEXT), contract.chars)
                    )
            if (
                contract.per is not None
                and coverage.end > coverage.start
                and coverage.start // contract.per != (coverage.end - 1) // contract.per
            ):
                violations.append(ConstraintViolation(identity, "authored item page boundary"))
            if any(coverage.start < boundary < coverage.end for boundary in contract.required_ends):
                violations.append(ConstraintViolation(identity, "authored item page boundary"))
            ends = family.regions[identity].cuts
            if coverage.end == ends[-1]:
                break_shortfalls[(identity, coverage.end)] = int(
                    bool(node.units) and coverage.end - coverage.start < contract.widows
                )
            else:
                break_shortfalls[(identity, coverage.end)] = int(body_cost.get(Axis.DISPLAY_TEXT) < contract.min_fill)
            for goal in policies.get(identity, RegionPolicy()).goals:
                context.work.consume()
                if goal.axis not in evaluation.resource_axes:
                    violations.append(UnsupportedCapability(identity, f"allocation goal: {goal.axis.value}"))
                elif goal.hard:
                    extra += ResourceCost(
                        {goal.axis: max(0, goal.minimum - body_cost.get(goal.axis) - extra.get(goal.axis))}
                    )
            costs[identity] = body_cost + extra
        semantic = occurrence.graph.units[occurrence.reference].value
        character_budget = semantic if isinstance(semantic, Budgeted) else NativeTraits.of(semantic).budget
        if character_budget is not None:
            actual = cost.get(Axis.DISPLAY_TEXT) - navigation_chars[occurrence.identity]
            extra += ResourceCost(
                {
                    Axis.DISPLAY_TEXT: max(
                        0,
                        budget_floor(occurrence.graph, occurrence.reference, occurrence.ancestors())
                        - actual
                        - extra.get(Axis.DISPLAY_TEXT),
                    )
                }
            )
            ceiling = character_budget.preferred + character_budget.stretch
            if actual + extra.get(Axis.DISPLAY_TEXT) > ceiling:
                violations.append(
                    CapacityExceeded(
                        occurrence.identity,
                        Axis.DISPLAY_TEXT,
                        actual + extra.get(Axis.DISPLAY_TEXT),
                        ceiling,
                    )
                )
        if isinstance(semantic, Budget):
            violations.extend(
                CapacityExceeded(occurrence.identity, axis, used, cap)
                for axis, used, cap in (cost + extra).over(semantic.limits)
            )
        return extra

    extra = visit(visible)
    violations.extend(
        CapacityExceeded(visible.offer.occurrence.identity, axis, used, cap)
        for axis, used, cap in (evaluation.cost + extra).over(context.capacities)
    )
    return tuple(violations), costs, break_shortfalls


class FamilyCertifier[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT]:
    """Certify families within one search, retaining progress if its allowance expires."""

    def __init__(
        self,
        rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
        context: PlanningContext,
        policies: Mapping[OccurrenceId, RegionPolicy],
        finalize: Callable[[BuiltComposition[ResourceT, BodyT, PreparationT, ContextT], PlanningContext], OutputT],
    ) -> None:
        self.rules = rules
        self.context = context
        self.policies = policies
        self.finalize = finalize
        self.certified_combinations = 0

    def certify(
        self,
        family: PageFamily[RenderTargetT, ContextT, PayloadT],
        regions: Mapping[OccurrenceId, FragmentTree[RenderTargetT, ContextT, PayloadT]],
    ) -> FamilyCertificate[OutputT] | FamilyRejection:
        """Return a complete certificate or the first infeasible page's violations."""
        peaks: dict[OccurrenceId, ResourceCost] = {}
        page_violations: dict[tuple[OccurrenceId, int], int] = {}
        pages: list[CertifiedPage[OutputT]] = []
        selected: dict[OccurrenceId, int] | None = None
        if self.context.local_view:
            resolutions = (
                RevealResolver(family.root.offer.occurrence.graph, self.context.work.consume).resolve(
                    tuple(self.context.presentation.pending_reveals.values()),
                    family.regions,
                    self.context.presentation.pending_position_sequence,
                )
                if self.context.presentation.pending_reveals
                else ()
            )
            desired_reveals = {
                region: page for resolution in resolutions for region, page in resolution.coordinates.items()
            }
            selected = {}
            for identity, region in family.regions.items():
                contract = regions[identity].candidate.offer.region
                assert contract is not None
                owner = contract.owner
                key = contract.cursor_key or contract.key
                if identity in self.context.page_overrides:
                    page = self.context.page_overrides[identity]
                elif key in self.context.positions:
                    page = self.context.positions[key].page
                elif identity in desired_reveals:
                    page = desired_reveals[identity]
                elif isinstance(owner, Controlled | RoutedPage):
                    page = resolve_page(owner.value, len(region.pages))
                else:
                    position = self.context.positions.get(key) or self.context.presentation.pending_positions.get(key)
                    if position is None and key in self.context.presentation.cursors:
                        position = self.context.presentation.cursor(key).position
                    page = position.page if position is not None else resolve_page(owner.initial, len(region.pages))
                selected[identity] = min(max(0, page), len(region.pages) - 1)
        layout = FamilyLayout(family, self.context, selected)
        snapshot = layout.snapshot if self.context.inspect_layout else None
        for coordinates, visible in family.combinations(self.context, selected):
            self.context.work.consume()
            if family.regions:
                try:
                    visible = family.contextualize(visible, layout.for_view(dict(coordinates)), self.context)
                except FactUnavailableError as error:
                    return FamilyRejection((ConstraintViolation(visible.offer.occurrence.identity, str(error)),))
            try:
                admitted = evaluate_composition(visible, self.rules, self.context)
            except SharedResourceConflictError as error:
                # Conflict is contextual: separate pages may use different
                # values of one identity. Only coexistence is forbidden.
                admitted = Evaluation(
                    violations=(
                        ConstraintViolation(
                            visible.offer.occurrence.identity, f"shared resource conflict: {error.identity}"
                        ),
                    )
                )
            if isinstance(admitted, Evaluation):
                self.context.work.consume(len(admitted.violations))
                return FamilyRejection(admitted.violations)
            failures, costs, shortfalls = _allocation_checks(
                visible, admitted.evaluation, family, regions, self.policies, self.context
            )
            if failures:
                self.context.work.consume(len(failures))
                return FamilyRejection(failures)
            for identity, cost in costs.items():
                self.context.work.consume()
                previous = peaks.get(identity, EMPTY_COST)
                peaks[identity] = ResourceCost(
                    {axis: max(cost.get(axis), previous.get(axis)) for axis in {*cost.values, *previous.values}}
                )
            # Count each logical page once, even across many sibling combinations.
            for key, shortfall in shortfalls.items():
                self.context.work.consume()
                page_violations[key] = max(page_violations.get(key, 0), shortfall)
            built = construct_composition(admitted, self.rules, self.context)
            if snapshot is not None:
                built = replace(
                    built,
                    layout=snapshot.context(dict(coordinates), complete=not self.context.local_view),
                    choices=snapshot.choices,
                )
            self.context.work.consume()
            pages.append(CertifiedPage(coordinates, self.finalize(built, self.context)))
            self.certified_combinations += 1
        return FamilyCertificate(peaks, page_violations, tuple(pages))

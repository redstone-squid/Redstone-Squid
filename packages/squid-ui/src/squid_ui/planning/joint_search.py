"""Bounded joint search over representations and stable legal page partitions.

The queue retains partial assignments and exact partition states. Fit estimates
only order probes; rejection uses complete, contextual evaluations. The sole
partition bound discards refinements of a feasible family with satisfied soft
goals: they preserve its choices and effects and necessarily add page turns.
Opaque payloads and scalar resource envelopes never establish dominance.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from itertools import count
from types import MappingProxyType

from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.planning.allocation import CertifiedAllocation, CertifiedPage, PageFamily, allocate_family
from squid_ui.planning.allocation_policy import (
    AllocationQuality,
    PreferenceTree,
    RegionPolicy,
    SearchCompletion,
    SearchDiagnostics,
    compare_quality,
    dense_ranks,
)
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CandidateViolation,
    CapacityExceeded,
    DiscoveryCompletion,
    Evaluation,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
    construct_composition,
    evaluate_composition,
)
from squid_ui.planning.fragments import FragmentTree, fragment_tree
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.presentations import Budget, FidelityEffect


class JointInfeasibleError(UnsolvableLayoutError):
    def __init__(self, diagnostics: SearchDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("no declared joint allocation is feasible")


class JointUnsupportedError(LayoutError):
    def __init__(self, diagnostics: SearchDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("joint allocation requires unsupported vocabulary or resource axes")


class JointExhaustedError(LayoutSearchExhaustedError):
    def __init__(self, diagnostics: SearchDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__("joint allocation exhausted its allowance before certifying a complete page family")


@dataclass(frozen=True, slots=True)
class JointResult[RenderTargetT, ContextT, PayloadT, OutputT]:
    allocation: CertifiedAllocation[RenderTargetT, ContextT, PayloadT, OutputT]
    diagnostics: SearchDiagnostics
    nondominated: tuple[AllocationQuality, ...]


@dataclass(frozen=True, slots=True)
class _Assignment[RenderTargetT, ContextT, PayloadT]:
    pending: tuple[SemanticOccurrence[RenderTargetT, ContextT], ...]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...] = ()


@dataclass(frozen=True, slots=True)
class _Partitions[RenderTargetT, ContextT, PayloadT]:
    assignment: int
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...]
    regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]
    cuts: tuple[tuple[int, ...], ...]
    probe: bool = False


def _preferences[RenderTargetT, ContextT, PayloadT](
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    ranks: Mapping[OccurrenceId, int],
    policies: Mapping[OccurrenceId, RegionPolicy],
    context: PlanningContext,
) -> PreferenceTree:
    own: list[int] = []
    children: list[tuple[int, PreferenceTree]] = []

    def visit(part: CandidateComposition[RenderTargetT, ContextT, PayloadT]) -> None:
        context.work.consume()
        own.append(ranks[part.offer.occurrence.identity])
        for child in part.children:
            if child.offer.region is not None:
                priority = policies.get(child.offer.occurrence.identity, RegionPolicy()).priority
                children.append((priority, _preferences(child, ranks, policies, context)))
            else:
                visit(child)

    visit(candidate)
    return PreferenceTree(tuple(sorted(own, reverse=True)), tuple(children))


def _candidate[RenderTargetT, ContextT, PayloadT](
    occurrence: SemanticOccurrence[RenderTargetT, ContextT],
    choices: Mapping[OccurrenceId, RealizationOffer[RenderTargetT, ContextT, PayloadT]],
    context: PlanningContext,
) -> CandidateComposition[RenderTargetT, ContextT, PayloadT]:
    context.work.consume()
    offer = choices[occurrence.identity]
    return CandidateComposition(offer, tuple(_candidate(child, choices, context) for child in offer.children))


def _region_trees[RenderTargetT, ContextT, PayloadT](
    root: FragmentTree[RenderTargetT, ContextT, PayloadT], context: PlanningContext
) -> tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]:
    found: list[FragmentTree[RenderTargetT, ContextT, PayloadT]] = []
    pending = [root]
    while pending:
        context.work.consume()
        part = pending.pop()
        if part.candidate.offer.region is not None:
            found.append(part)
        pending.extend(reversed(part.children))
    return tuple(found)


def _allocation_checks[RenderTargetT, ContextT, PayloadT, ResourceT, PreparationT](
    visible: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    evaluation: Evaluation[ResourceT, PreparationT],
    family: PageFamily[RenderTargetT, ContextT, PayloadT],
    regions: Mapping[OccurrenceId, FragmentTree[RenderTargetT, ContextT, PayloadT]],
    policies: Mapping[OccurrenceId, RegionPolicy],
    context: PlanningContext,
) -> tuple[tuple[CandidateViolation, ...], dict[OccurrenceId, ResourceCost], tuple[int, ...]]:
    violations: list[CandidateViolation] = []
    costs: dict[OccurrenceId, ResourceCost] = {}
    break_shortfalls: list[int] = []

    def visit(part: CandidateComposition[RenderTargetT, ContextT, PayloadT]) -> ResourceCost:
        context.work.consume()
        extra = EMPTY_COST
        for child in part.children:
            extra += visit(child)
        occurrence = part.offer.occurrence
        coverage = occurrence.fragment
        identity = coverage.logical if coverage is not None else occurrence.identity
        cost = evaluation.scoped_costs.get(occurrence.identity, EMPTY_COST)
        if identity in regions and coverage is not None and occurrence.placement.value != "navigation":
            node = regions[identity]
            contract = node.candidate.offer.region
            assert contract is not None
            # A character grant covers content and authored chrome. Navigation has
            # its own exact cost in the enclosing composition.
            navigation = sum(
                evaluation.scoped_costs.get(child.offer.occurrence.identity, EMPTY_COST).get(Axis.DISPLAY_TEXT)
                for child in part.children
                if child.offer.occurrence.placement.value == "navigation"
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
            ends = family.regions[identity].cuts
            if coverage.end == ends[-1]:
                break_shortfalls.append(max(0, contract.widows - (coverage.end - coverage.start)) if node.units else 0)
            else:
                break_shortfalls.append(max(0, contract.min_fill - body_cost.get(Axis.DISPLAY_TEXT)))
            for goal in policies.get(identity, RegionPolicy()).goals:
                context.work.consume()
                if goal.axis not in evaluation.resource_axes:
                    violations.append(UnsupportedCapability(identity, f"allocation goal: {goal.axis.value}"))
                elif goal.hard:
                    extra += ResourceCost(
                        {goal.axis: max(0, goal.minimum - body_cost.get(goal.axis) - extra.get(goal.axis))}
                    )
        semantic = occurrence.graph.units[occurrence.reference].value
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
    return tuple(violations), costs, tuple(break_shortfalls)


def search_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT](
    root: SemanticOccurrence[RenderTargetT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
    *,
    finalize: Callable[[BuiltComposition[ResourceT, BodyT, PreparationT, ContextT], PlanningContext], OutputT],
    selections: Mapping[OccurrenceId, str] | None = None,
    policies: Mapping[OccurrenceId, RegionPolicy] | None = None,
    partitions: Mapping[OccurrenceId, tuple[int, ...]] | None = None,
) -> JointResult[RenderTargetT, ContextT, PayloadT, OutputT]:
    """Certify a nondominated allocation without publishing bindings or state.

    Fixed selections/partitions constrain the domain for reproducible tests. The
    finalizer runs within the allowance, before an allocation can become an
    incumbent. All reachable outputs are built, so selection and bounded search
    are independent of the requested cursor. Only terminal status is added after
    exhaustion.
    """
    selected = selections or {}
    policy = policies or {}
    fixed_cuts = partitions or {}
    serials = count()
    queue: list[
        tuple[
            int,
            tuple[int, ...],
            int,
            int,
            int,
            _Assignment[RenderTargetT, ContextT, PayloadT] | _Partitions[RenderTargetT, ContextT, PayloadT],
        ]
    ] = []
    retained: list[tuple[AllocationQuality, CertifiedAllocation[RenderTargetT, ContextT, PayloadT, OutputT]]] = []
    unsupported: set[OccurrenceId] = set()
    violations: list[CandidateViolation] = []
    generated = evaluations = certified = 0
    exhausted = False
    seen_partitions: set[tuple[int, tuple[tuple[int, ...], ...]]] = set()
    seen_occurrences: set[OccurrenceId] = set()
    seen_regions: set[OccurrenceId] = set()

    def push(
        task: _Assignment[RenderTargetT, ContextT, PayloadT] | _Partitions[RenderTargetT, ContextT, PayloadT],
    ) -> None:
        nonlocal generated
        context.work.consume(1 + len(task.choices))
        if isinstance(task, _Partitions):
            identity = (task.assignment, task.cuts)
            if identity in seen_partitions:
                return
            seen_partitions.add(identity)
        generated += 1
        ranks = tuple(sorted((rank for _, rank in task.choices if rank), reverse=True))
        turns = sum(len(cuts) - 1 for cuts in task.cuts) if isinstance(task, _Partitions) else 0
        probe = isinstance(task, _Partitions) and task.probe
        heappush(queue, (0 if probe else 1, ranks, turns, -len(task.choices), next(serials), task))

    try:
        push(_Assignment((root,)))
        while queue:
            context.work.consume()
            _, _, _, _, _, task = heappop(queue)
            if isinstance(task, _Assignment):
                if task.pending:
                    occurrence, *rest = task.pending
                    seen_occurrences.add(occurrence.identity)
                    context.work.consume()
                    discovery = rules.offers(occurrence, context)
                    if isinstance(discovery, OfferDiscovery):
                        offers = discovery.offers
                        if discovery.completion == DiscoveryCompletion.EXHAUSTED:
                            exhausted = True
                        elif discovery.completion == DiscoveryCompletion.UNSUPPORTED:
                            unsupported.add(occurrence.identity)
                    else:
                        offers = discovery
                        if not offers:
                            unsupported.add(occurrence.identity)
                    context.work.consume(len(offers))
                    ranks = dense_ranks(tuple(offer.preference for offer in offers))
                    keys: set[str] = set()
                    for offer, rank in sorted(
                        zip(offers, ranks, strict=True), key=lambda entry: (entry[1], entry[0].key)
                    ):
                        context.work.consume()
                        if offer.key in keys or offer.occurrence != occurrence:
                            message = "offers must have unique keys and retain their requested occurrence"
                            raise LayoutInvariantError(message)
                        keys.add(offer.key)
                        if occurrence.identity in selected and selected[occurrence.identity] != offer.key:
                            continue
                        context.work.consume(len(task.choices))
                        if any(previous.occurrence.identity == occurrence.identity for previous, _ in task.choices):
                            message = "cyclic or duplicate occurrence in offer structure"
                            raise LayoutInvariantError(message)
                        push(_Assignment((*offer.children, *rest), (*task.choices, (offer, rank))))
                    if occurrence.identity in selected and selected[occurrence.identity] not in keys:
                        message = f"unknown fixed offer for {occurrence.identity.path!r}"
                        raise LayoutInvariantError(message)
                    continue
                context.work.consume(len(task.choices))
                choices = {offer.occurrence.identity: offer for offer, _ in task.choices}
                candidate = _candidate(root, choices, context)
                regions = _region_trees(fragment_tree(candidate, context), context)
                seen_regions.update(node.candidate.offer.occurrence.identity for node in regions)
                cuts = tuple(
                    fixed_cuts.get(node.candidate.offer.occurrence.identity, (len(node.units),)) for node in regions
                )
                push(_Partitions(next(serials), candidate, task.choices, regions, cuts, probe=True))
                continue

            context.work.consume()
            family = allocate_family(task.candidate, task.regions, task.cuts, context)
            region_map = {node.candidate.offer.occurrence.identity: node for node in task.regions}
            peaks: dict[OccurrenceId, ResourceCost] = {}
            break_shortfalls: tuple[int, ...] = ()
            pages: list[CertifiedPage[OutputT]] = []
            feasible = True
            for coordinates, visible in family.combinations(context):
                context.work.consume()
                evaluations += 1
                admitted = evaluate_composition(visible, rules, context)
                if isinstance(admitted, Evaluation):
                    context.work.consume(len(admitted.violations))
                    violations.extend(admitted.violations)
                    unsupported.update(
                        v.occurrence for v in admitted.violations if isinstance(v, UnsupportedCapability)
                    )
                    feasible = False
                    break
                failures, costs, shortfalls = _allocation_checks(
                    visible, admitted.evaluation, family, region_map, policy, context
                )
                if failures:
                    context.work.consume(len(failures))
                    violations.extend(failures)
                    unsupported.update(v.occurrence for v in failures if isinstance(v, UnsupportedCapability))
                    feasible = False
                    break
                for identity, cost in costs.items():
                    context.work.consume()
                    previous = peaks.get(identity, EMPTY_COST)
                    peaks[identity] = ResourceCost(
                        {axis: max(cost.get(axis), previous.get(axis)) for axis in {*cost.values, *previous.values}}
                    )
                # Worst visible break shortfalls, without rewarding hidden regions.
                break_shortfalls = max(break_shortfalls, tuple(sorted(shortfalls, reverse=True)))
                built = construct_composition(admitted, rules, context)
                context.work.consume()
                output = finalize(built, context)
                pages.append(CertifiedPage(coordinates, output))
                certified += 1
            satisfied = False
            if feasible:
                context.work.consume(1 + len(task.choices) + len(region_map) + len(policy) + len(retained))
                ranks = {offer.occurrence.identity: rank for offer, rank in task.choices}
                preferences = _preferences(task.candidate, ranks, policy, context)
                minimums: list[int] = []
                for axis in Axis:
                    context.work.consume(1 + sum(len(value.goals) for value in policy.values()))
                    deficits = [
                        max(0, goal.minimum - peaks.get(identity, EMPTY_COST).get(axis))
                        for identity in region_map
                        for goal in policy.get(identity, RegionPolicy()).goals
                        if goal.axis == axis and not goal.hard
                    ]
                    minimums.extend(sorted(deficits, reverse=True))
                effects: frozenset[FidelityEffect] = frozenset(
                    effect for offer, _ in task.choices for effect in offer.effects
                )
                tie = tuple(
                    sorted(
                        (
                            offer.occurrence.identity.path,
                            offer.key,
                            family.regions[offer.occurrence.identity].cuts
                            if offer.occurrence.identity in family.regions
                            else (),
                        )
                        for offer, _ in task.choices
                    )
                )
                quality = AllocationQuality(
                    tuple(minimums),
                    preferences,
                    effects,
                    break_shortfalls,
                    sum(len(cuts) - 1 for cuts in task.cuts),
                    tie,
                )
                satisfied = not any(minimums) and not any(break_shortfalls)
                if not any(
                    compare_quality(previous, quality) == -1
                    or (compare_quality(previous, quality) == 0 and previous.tie <= quality.tie)
                    for previous, _ in retained
                ):
                    context.work.consume(len(retained) + len(pages) + len(region_map) + len(task.choices))
                    retained = [
                        (previous, allocation)
                        for previous, allocation in retained
                        if compare_quality(quality, previous) not in (-1, 0)
                    ]
                    family = replace(
                        family,
                        regions=MappingProxyType(
                            {
                                identity: replace(region, allocation=peaks.get(identity, EMPTY_COST))
                                for identity, region in family.regions.items()
                            }
                        ),
                    )
                    allocation = CertifiedAllocation(
                        family,
                        tuple(pages),
                        tuple(
                            sorted(
                                ((offer.occurrence.identity, offer.key) for offer, _ in task.choices),
                                key=lambda item: item[0].path,
                            )
                        ),
                    )
                    retained.append((quality, allocation))
            if feasible and satisfied:
                continue
            if task.probe:
                # Balanced refinement is only a work-order heuristic. Every legal
                # cut is still enqueued below; no fit assumption prunes that domain.
                refined: list[tuple[int, ...]] = []
                for node, ends in zip(task.regions, task.cuts, strict=True):
                    context.work.consume(1 + len(node.legal_ends) + len(ends))
                    available = [end for end in node.legal_ends if end not in ends]
                    if node.candidate.offer.occurrence.identity in fixed_cuts or not available:
                        refined.append(ends)
                    else:
                        start, end = max(zip((0, *ends[:-1]), ends, strict=True), key=lambda pair: pair[1] - pair[0])
                        middle = min(available, key=lambda cut: (abs(cut - (start + end) / 2), cut))
                        refined.append(tuple(sorted((*ends, middle))))
                if tuple(refined) != task.cuts:
                    push(replace(task, cuts=tuple(refined), probe=True))
            for index, node in enumerate(task.regions):
                if node.candidate.offer.occurrence.identity in fixed_cuts:
                    continue
                for end in node.legal_ends[:-1]:
                    context.work.consume()
                    if end not in task.cuts[index]:
                        changed = (*task.cuts[:index], tuple(sorted((*task.cuts[index], end))), *task.cuts[index + 1 :])
                        push(replace(task, cuts=changed, probe=False))
    except LayoutSearchExhaustedError:
        exhausted = True

    if not exhausted and (selected.keys() - seen_occurrences or (policy.keys() | fixed_cuts.keys()) - seen_regions):
        message = "joint search options contain unknown occurrences"
        raise LayoutInvariantError(message)
    completion = (
        SearchCompletion.EXHAUSTED
        if exhausted
        else SearchCompletion.UNSUPPORTED
        if unsupported
        else SearchCompletion.COMPLETE
    )
    diagnostics = SearchDiagnostics(
        completion,
        not exhausted and not unsupported,
        context.work.spent,
        context.work.limit,
        generated,
        evaluations,
        certified,
        tuple(violations),
        tuple(sorted(unsupported, key=lambda identity: identity.path)),
    )
    if not retained:
        if exhausted:
            raise JointExhaustedError(diagnostics)
        if unsupported:
            raise JointUnsupportedError(diagnostics)
        raise JointInfeasibleError(diagnostics)
    quality, allocation = min(retained, key=lambda entry: entry[0].tie)
    return JointResult(
        allocation, diagnostics, tuple(entry[0] for entry in sorted(retained, key=lambda entry: entry[0].tie))
    )

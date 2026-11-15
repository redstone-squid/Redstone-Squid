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

from squid_ui.authoring.presentations import Budget, FidelityEffect
from squid_ui.authoring.semantic import Budgeted
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
    ConstraintViolation,
    DiscoveryCompletion,
    Evaluation,
    OccurrenceId,
    OfferDiscovery,
    Placement,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    SharedResourceConflictError,
    TargetRules,
    UnsupportedCapability,
    construct_composition,
    evaluate_composition,
)
from squid_ui.planning.fragments import FragmentTree, fragment_tree
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.semantics import budget_floor
from squid_ui.primitives.nodes import Budget as NativeBudget


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
        self.context.update(
            work_used=diagnostics.work_used,
            work_limit=diagnostics.work_limit,
            generated_states=diagnostics.generated_states,
            target_evaluations=diagnostics.target_evaluations,
            certified_combinations=diagnostics.certified_combinations,
        )


@dataclass(frozen=True, slots=True)
class JointResult[RenderTargetT, ContextT, PayloadT, OutputT]:
    allocation: CertifiedAllocation[RenderTargetT, ContextT, PayloadT, OutputT]
    diagnostics: SearchDiagnostics
    nondominated: tuple[AllocationQuality, ...]


@dataclass(frozen=True, slots=True)
class _Assignment[RenderTargetT, ContextT, PayloadT]:
    pending: tuple[SemanticOccurrence[RenderTargetT, ContextT], ...]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...] = ()
    probe: bool = False
    fraction: float = 1.0
    repair: Mapping[OccurrenceId, str] | None = None


@dataclass(frozen=True, slots=True)
class _Partitions[RenderTargetT, ContextT, PayloadT]:
    assignment: int
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...]
    regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]
    cuts: tuple[tuple[int, ...], ...]
    probe: bool = False
    expand_only: bool = False
    repair: bool = False


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
            if child.offer.region is not None or child.offer.preference_priority is not None:
                priority = policies.get(
                    child.offer.occurrence.identity, RegionPolicy(priority=child.offer.preference_priority or 0)
                ).priority
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
        if isinstance(semantic, Budgeted | NativeBudget):
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
            ceiling = semantic.preferred + semantic.stretch
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


def search_composition[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT](
    root: SemanticOccurrence[RenderTargetT, ContextT],
    rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
    context: PlanningContext,
    *,
    finalize: Callable[[BuiltComposition[ResourceT, BodyT, PreparationT, ContextT], PlanningContext], OutputT],
    selections: Mapping[OccurrenceId, str] | None = None,
    policies: Mapping[OccurrenceId, RegionPolicy] | None = None,
    partitions: Mapping[OccurrenceId, tuple[int, ...]] | None = None,
    partition_hint: Mapping[OccurrenceId, tuple[int, ...]] | None = None,
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
    generated = certified = 0
    initial_evaluations = context.work.target_evaluations
    exhausted = False
    seen_partitions: set[tuple[int, tuple[tuple[int, ...], ...], bool]] = set()
    seen_occurrences: set[OccurrenceId] = set()
    seen_regions: set[OccurrenceId] = set()
    degradation_probed = False
    choice_counts: dict[OccurrenceId, int] = {}
    discoveries: dict[
        int,
        tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
        | OfferDiscovery[RenderTargetT, ContextT, PayloadT],
    ] = {}
    discovery_owners: dict[int, SemanticOccurrence[RenderTargetT, ContextT]] = {}
    repair_started = False

    def push(
        task: _Assignment[RenderTargetT, ContextT, PayloadT] | _Partitions[RenderTargetT, ContextT, PayloadT],
    ) -> None:
        nonlocal generated
        context.work.consume(1 + len(task.choices))
        if isinstance(task, _Partitions):
            identity = (task.assignment, task.cuts, task.expand_only)
            if identity in seen_partitions:
                return
            seen_partitions.add(identity)
        generated += 1
        ranks = tuple(sorted((rank for _, rank in task.choices if rank), reverse=True))
        turns = sum(len(cuts) - 1 for cuts in task.cuts) if isinstance(task, _Partitions) else 0
        probe = task.probe
        # Finish a probe before discovering every other probe's large offer ladder.
        # Candidate quality still compares the original preferences after certification.
        heappush(
            queue,
            (
                -1 if probe and task.repair else 0 if probe else 1,
                () if probe else ranks,
                turns,
                -len(task.choices),
                next(serials),
                task,
            ),
        )

    def expand(task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> None:
        for index, node in enumerate(task.regions):
            if node.candidate.offer.occurrence.identity in fixed_cuts:
                continue
            for end in node.legal_ends[:-1]:
                context.work.consume()
                if end not in task.cuts[index]:
                    changed = (*task.cuts[:index], tuple(sorted((*task.cuts[index], end))), *task.cuts[index + 1 :])
                    push(replace(task, cuts=changed, probe=False, expand_only=False))

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
                    # An occurrence's immutable context is shared by queued assignments.
                    # Keep its owner alive so object identities cannot be recycled.
                    discovery = discoveries.get(id(occurrence))
                    if discovery is None:
                        discovery = rules.offers(
                            occurrence,
                            replace(context, palette=occurrence.palette) if occurrence.palette is not None else context,
                        )
                        discoveries[id(occurrence)] = discovery
                        discovery_owners[id(occurrence)] = occurrence
                    if isinstance(discovery, OfferDiscovery):
                        offers = discovery.offers
                        if discovery.completion == DiscoveryCompletion.EXHAUSTED:
                            exhausted = True
                        elif discovery.completion == DiscoveryCompletion.UNSUPPORTED:
                            unsupported.add(occurrence.identity)
                            if discovery.limitation is not None:
                                violations.append(UnsupportedCapability(occurrence.identity, discovery.limitation))
                    else:
                        offers = discovery
                        if not offers:
                            unsupported.add(occurrence.identity)
                    context.work.consume(len(offers))
                    ranks = dense_ranks(tuple(offer.preference for offer in offers))
                    keys: set[str] = set()
                    eligible_count = 0
                    for offer in offers:
                        context.work.consume()
                        if offer.key in keys or offer.occurrence != occurrence:
                            message = "offers must have unique keys and retain their requested occurrence"
                            raise LayoutInvariantError(message)
                        keys.add(offer.key)
                        if (occurrence.identity not in selected or offer.key == selected[occurrence.identity]) and not (
                            context.strict and offer.effects
                        ):
                            eligible_count += 1
                    choice_counts[occurrence.identity] = eligible_count
                    ordered = sorted(zip(offers, ranks, strict=True), key=lambda entry: (entry[1], entry[0].key))
                    if task.probe:
                        eligible = [
                            entry
                            for entry in ordered
                            if (occurrence.identity not in selected or entry[0].key == selected[occurrence.identity])
                            and not (context.strict and entry[0].effects)
                        ]
                        if task.repair is not None:
                            preferred = task.repair.get(occurrence.identity)
                            ordered = next(([entry] for entry in eligible if entry[0].key == preferred), eligible[:1])
                        else:
                            position = min(len(eligible) - 1, int((len(eligible) - 1) * task.fraction + 0.5))
                            ordered = eligible[position : position + 1] if eligible else []
                    for offer, rank in ordered:
                        if occurrence.identity in selected and selected[occurrence.identity] != offer.key:
                            continue
                        if context.strict and offer.effects:
                            continue
                        context.work.consume(len(task.choices))
                        if any(previous.occurrence.identity == occurrence.identity for previous, _ in task.choices):
                            message = "cyclic or duplicate occurrence in offer structure"
                            raise LayoutInvariantError(message)
                        push(
                            _Assignment(
                                (*offer.children, *rest),
                                (*task.choices, (offer, rank)),
                                probe=task.probe,
                                fraction=task.fraction,
                                repair=task.repair,
                            )
                        )
                    if occurrence.identity in selected and selected[occurrence.identity] not in keys:
                        message = f"unknown fixed offer for {occurrence.identity.path!r}"
                        raise LayoutInvariantError(message)
                    continue
                context.work.consume(len(task.choices))
                choices = {offer.occurrence.identity: offer for offer, _ in task.choices}
                if selected.keys() - choices.keys():
                    continue
                candidate = _candidate(root, choices, context)
                regions = _region_trees(fragment_tree(candidate, context), context)
                seen_regions.update(node.candidate.offer.occurrence.identity for node in regions)
                if fixed_cuts.keys() - {node.candidate.offer.occurrence.identity for node in regions}:
                    continue
                initial: list[tuple[int, ...]] = []
                for node in regions:
                    contract = node.candidate.offer.region
                    assert contract is not None
                    required = contract.required_ends or (
                        tuple(range(contract.per, len(node.units), contract.per)) if contract.per else ()
                    )
                    required = tuple(end for end in required if end < len(node.units))
                    initial.append(
                        fixed_cuts.get(node.candidate.offer.occurrence.identity, (*required, len(node.units)))
                    )
                cuts = tuple(initial)
                assignment = next(serials)
                if partition_hint:
                    hinted: list[tuple[int, ...]] = []
                    for node, required in zip(regions, cuts, strict=True):
                        identity = node.candidate.offer.occurrence.identity
                        ends = partition_hint.get(identity, required)
                        context.work.consume(1 + len(ends) + len(node.legal_ends) + len(required))
                        legal = set(node.legal_ends) | {len(node.units)}
                        if (
                            ends != tuple(sorted(set(ends)))
                            or not ends
                            or ends[-1] != len(node.units)
                            or not set(ends) <= legal
                            or not set(required) <= set(ends)
                            or (identity in fixed_cuts and ends != required)
                        ):
                            ends = required
                        hinted.append(ends)
                    push(_Partitions(assignment, candidate, task.choices, regions, tuple(hinted), probe=True))
                push(
                    _Partitions(
                        assignment, candidate, task.choices, regions, cuts, probe=True, repair=task.repair is not None
                    )
                )
                # Try preserving every sibling while splitting just one region. Balanced
                # probes alone can miss a small feasible family when both pagers' chrome
                # cannot coexist. This only changes search order, not admissible cuts.
                if len(regions) > 1:
                    for index, node in enumerate(regions):
                        if node.candidate.offer.occurrence.identity not in fixed_cuts and len(node.legal_ends) <= 64:
                            unilateral = (*cuts[:index], node.legal_ends or (0,), *cuts[index + 1 :])
                            push(_Partitions(assignment, candidate, task.choices, regions, unilateral, probe=True))
                continue

            if task.expand_only:
                expand(task)
                continue
            context.work.consume()
            family = allocate_family(task.candidate, task.regions, task.cuts, context)
            region_map = {node.candidate.offer.occurrence.identity: node for node in task.regions}
            peaks: dict[OccurrenceId, ResourceCost] = {}
            page_violations: dict[tuple[OccurrenceId, int], int] = {}
            pages: list[CertifiedPage[OutputT]] = []
            feasible = True
            for coordinates, visible in family.combinations(context):
                context.work.consume()
                try:
                    admitted = evaluate_composition(visible, rules, context)
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
                # Count each logical page once, even across many sibling combinations.
                for key, shortfall in shortfalls.items():
                    context.work.consume()
                    page_violations[key] = max(page_violations.get(key, 0), shortfall)
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
                        if identity in region_map
                        else 0
                        for identity, region_policy in policy.items()
                        for goal in region_policy.goals
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
                break_shortfalls = tuple(sorted(page_violations.values(), reverse=True))
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
                    context.work.consume(sum(1 + len(page.coordinates) for page in pages))
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
            if not feasible and (task.repair or not repair_started):
                # Try one structural step at a time before the broad compact
                # probes. This finds useful fits under component pressure without
                # shortening every independent text ladder. Failed probes never prune.
                repair_started = True
                steps: list[tuple[int, int, tuple[str, ...], RealizationOffer[RenderTargetT, ContextT, PayloadT]]] = []
                for current, rank in task.choices:
                    if current.occurrence.identity in selected:
                        continue
                    discovery = discoveries[id(current.occurrence)]
                    domain = discovery.offers if isinstance(discovery, OfferDiscovery) else discovery
                    context.work.consume(len(domain))
                    domain_ranks = dense_ranks(tuple(offer.preference for offer in domain))
                    better = [
                        (offer_rank, offer)
                        for offer, offer_rank in zip(domain, domain_ranks, strict=True)
                        if offer_rank > rank
                        and (not context.strict or not offer.effects)
                        and (not offer.effects or current.children or offer.children)
                    ]
                    if better:
                        next_rank, next_offer = min(better, key=lambda entry: (entry[0], entry[1].key))
                        steps.append(
                            (current.preference_priority or 0, next_rank, current.occurrence.identity.path, next_offer)
                        )
                if steps:
                    _, _, _, changed = min(steps, key=lambda entry: entry[:3])
                    context.work.consume(len(task.choices))
                    hints = {offer.occurrence.identity: offer.key for offer, _ in task.choices}
                    hints[changed.occurrence.identity] = changed.key
                    push(_Assignment((root,), probe=True, repair=hints))
            if feasible and satisfied:
                continue
            if not feasible and not degradation_probed:
                # Find a compact certificate before enumerating combinations of
                # independent ladders. This changes work order only: ordinary
                # assignments remain queued and still establish optimality.
                degradation_probed = True
                # Intermediate rungs avoid an empty incumbent when several prose
                # blocks must share space. This samples work order, never feasibility.
                context.work.consume(len(task.choices))
                adjustable = 0
                for current, _ in task.choices:
                    discovery = discoveries[id(current.occurrence)]
                    domain = discovery.offers if isinstance(discovery, OfferDiscovery) else discovery
                    context.work.consume(len(domain))
                    if choice_counts.get(current.occurrence.identity, 0) > 1 and (
                        not task.regions or any(offer.effects for offer in domain)
                    ):
                        adjustable += 1
                if adjustable:
                    for fraction in dict.fromkeys((1.0, 1 - 1 / max(2, adjustable), 0.5, 0.75, 0.875, 0.9375)):
                        push(_Assignment((root,), probe=True, fraction=fraction))
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
                # Certify the next probe before spending the allowance on its many
                # alternatives. Deferred expansion still explores every legal cut.
                push(replace(task, probe=False, expand_only=True))
            else:
                expand(task)
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
        context.work.target_evaluations - initial_evaluations,
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

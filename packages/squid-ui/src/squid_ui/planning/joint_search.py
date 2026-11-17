"""Bounded joint search over representations and stable legal page partitions.

The queue retains partial assignments and exact partition states. Fit estimates
only order probes; rejection uses complete, contextual evaluations. An assignment
can be skipped when a certified family beats even its best possible quality:
zero shortfalls and no page turns. Feasible families with satisfied soft goals
also need no partition refinements, which necessarily add page turns.
Opaque payloads and scalar resource envelopes never establish dominance.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from itertools import count
from types import MappingProxyType

from squid_ui.authoring.presentations import FidelityEffect
from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.planning.allocation import AllocationSnapshot, CertifiedPage, PageFamily, allocate_family
from squid_ui.planning.allocation_policy import (
    AllocationQuality,
    PreferenceTree,
    RegionPolicy,
    SearchCompletion,
    SearchDiagnostics,
    compare_quality,
    dense_ranks,
)
from squid_ui.planning.certification import FamilyCertifier, FamilyRejection
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CandidateViolation,
    DiscoveryCompletion,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.fragments import FragmentTree, fragment_tree
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost


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
class JointResult[OutputT]:
    allocation: AllocationSnapshot[OutputT]
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


class _JointSearch[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT]:
    """One run of `search_composition`, with the queue and bookkeeping its phases share.

    The queue holds two kinds of task. An `_Assignment` chooses one offer per occurrence, depth first;
    once every occurrence has an offer, it becomes `_Partitions` tasks that try region page cuts.
    Certifying a partition evaluates every page combination and retains the allocation if no retained
    allocation dominates it. Probe tasks only change the order of work: every legal choice and cut is
    still queued, so the result does not depend on the probes.
    """

    def __init__(
        self,
        root: SemanticOccurrence[RenderTargetT, ContextT],
        rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
        context: PlanningContext,
        *,
        finalize: Callable[[BuiltComposition[ResourceT, BodyT, PreparationT, ContextT], PlanningContext], OutputT],
        selections: Mapping[OccurrenceId, str] | None,
        policies: Mapping[OccurrenceId, RegionPolicy] | None,
        partitions: Mapping[OccurrenceId, tuple[int, ...]] | None,
        partition_hint: Mapping[OccurrenceId, tuple[int, ...]] | None,
    ) -> None:
        self.root = root
        self.rules = rules
        self.context = context
        self.finalize = finalize
        self.partition_hint = partition_hint
        self.selected = selections or {}
        self.policy = policies or {}
        self.fixed_cuts = partitions or {}
        self.serials = count()
        self.queue: list[
            tuple[
                int,
                tuple[int, ...],
                int,
                int,
                int,
                _Assignment[RenderTargetT, ContextT, PayloadT] | _Partitions[RenderTargetT, ContextT, PayloadT],
            ]
        ] = []
        self.retained: list[tuple[AllocationQuality, AllocationSnapshot[OutputT]]] = []
        self.certifier = FamilyCertifier(rules, context, self.policy, finalize)
        self.unsupported: set[OccurrenceId] = set()
        self.violations: list[CandidateViolation] = []
        self.generated = 0
        self.initial_evaluations = self.context.work.target_evaluations
        self.exhausted = False
        self.seen_partitions: set[tuple[int, tuple[tuple[int, ...], ...], bool]] = set()
        self.seen_occurrences: set[OccurrenceId] = set()
        self.seen_regions: set[OccurrenceId] = set()
        self.degradation_probed = False
        self.choice_counts: dict[OccurrenceId, int] = {}
        self.discoveries: dict[
            int,
            tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
            | OfferDiscovery[RenderTargetT, ContextT, PayloadT],
        ] = {}
        self.discovery_owners: dict[int, SemanticOccurrence[RenderTargetT, ContextT]] = {}
        self.repair_started = False

    def run(self) -> JointResult[OutputT]:
        try:
            self._push(_Assignment((self.root,)))
            while self.queue:
                self.context.work.consume()
                _, _, _, _, _, task = heappop(self.queue)
                if isinstance(task, _Assignment):
                    if task.pending:
                        self._branch(task)
                    else:
                        self._partition(task)
                    continue
                if task.expand_only:
                    self._expand(task)
                    continue
                feasible, satisfied = self._certify(task)
                if not feasible and (task.repair or not self.repair_started):
                    self._push_repair(task)
                if feasible and satisfied:
                    continue
                if not feasible and not self.degradation_probed:
                    self._push_degradation_probes(task)
                if task.probe:
                    self._refine_probe(task)
                else:
                    self._expand(task)
        except LayoutSearchExhaustedError:
            self.exhausted = True
        return self._result()

    def _push(
        self,
        task: _Assignment[RenderTargetT, ContextT, PayloadT] | _Partitions[RenderTargetT, ContextT, PayloadT],
    ) -> None:
        self.context.work.consume(1 + len(task.choices))
        if isinstance(task, _Partitions):
            identity = (task.assignment, task.cuts, task.expand_only)
            if identity in self.seen_partitions:
                return
            self.seen_partitions.add(identity)
        self.generated += 1
        ranks = tuple(sorted((rank for _, rank in task.choices if rank), reverse=True))
        turns = sum(len(cuts) - 1 for cuts in task.cuts) if isinstance(task, _Partitions) else 0
        probe = task.probe
        # Finish a probe before discovering every other probe's large offer ladder.
        # Candidate quality still compares the original preferences after certification.
        heappush(
            self.queue,
            (
                -1 if probe and task.repair else 0 if probe else 1,
                () if probe else ranks,
                turns,
                -len(task.choices),
                next(self.serials),
                task,
            ),
        )

    def _expand(self, task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> None:
        """Queue every partition that adds one more legal cut to one region."""
        for index, node in enumerate(task.regions):
            if node.candidate.offer.occurrence.identity in self.fixed_cuts:
                continue
            for end in node.legal_ends[:-1]:
                self.context.work.consume()
                if end not in task.cuts[index]:
                    changed = (*task.cuts[:index], tuple(sorted((*task.cuts[index], end))), *task.cuts[index + 1 :])
                    self._push(replace(task, cuts=changed, probe=False, expand_only=False))

    def _branch(self, task: _Assignment[RenderTargetT, ContextT, PayloadT]) -> None:
        """Queue one assignment per eligible offer for the task's next pending occurrence."""
        occurrence, *rest = task.pending
        self.seen_occurrences.add(occurrence.identity)
        self.context.work.consume()
        # An occurrence's immutable context is shared by queued assignments.
        # Keep its owner alive so object identities cannot be recycled.
        discovery = self.discoveries.get(id(occurrence))
        if discovery is None:
            discovery = self.rules.offers(
                occurrence,
                replace(self.context, palette=occurrence.palette) if occurrence.palette is not None else self.context,
            )
            self.discoveries[id(occurrence)] = discovery
            self.discovery_owners[id(occurrence)] = occurrence
        if isinstance(discovery, OfferDiscovery):
            offers = discovery.offers
            if discovery.completion == DiscoveryCompletion.EXHAUSTED:
                self.exhausted = True
            elif discovery.completion == DiscoveryCompletion.UNSUPPORTED:
                self.unsupported.add(occurrence.identity)
                if discovery.limitation is not None:
                    self.violations.append(UnsupportedCapability(occurrence.identity, discovery.limitation))
        else:
            offers = discovery
            if not offers:
                self.unsupported.add(occurrence.identity)
        self.context.work.consume(len(offers))
        ranks = dense_ranks(tuple(offer.preference for offer in offers))
        keys: set[str] = set()
        eligible_count = 0
        for offer in offers:
            self.context.work.consume()
            if offer.key in keys or offer.occurrence != occurrence:
                message = "offers must have unique keys and retain their requested occurrence"
                raise LayoutInvariantError(message)
            keys.add(offer.key)
            if (occurrence.identity not in self.selected or offer.key == self.selected[occurrence.identity]) and not (
                self.context.strict and offer.effects
            ):
                eligible_count += 1
        self.choice_counts[occurrence.identity] = eligible_count
        ordered = sorted(zip(offers, ranks, strict=True), key=lambda entry: (entry[1], entry[0].key))
        if task.probe:
            eligible = [
                entry
                for entry in ordered
                if (occurrence.identity not in self.selected or entry[0].key == self.selected[occurrence.identity])
                and not (self.context.strict and entry[0].effects)
            ]
            if task.repair is not None:
                preferred = task.repair.get(occurrence.identity)
                ordered = next(([entry] for entry in eligible if entry[0].key == preferred), eligible[:1])
            else:
                position = min(len(eligible) - 1, int((len(eligible) - 1) * task.fraction + 0.5))
                ordered = eligible[position : position + 1] if eligible else []
        for offer, rank in ordered:
            if occurrence.identity in self.selected and self.selected[occurrence.identity] != offer.key:
                continue
            if self.context.strict and offer.effects:
                continue
            self.context.work.consume(len(task.choices))
            if any(previous.occurrence.identity == occurrence.identity for previous, _ in task.choices):
                message = "cyclic or duplicate occurrence in offer structure"
                raise LayoutInvariantError(message)
            self._push(
                _Assignment(
                    (*offer.children, *rest),
                    (*task.choices, (offer, rank)),
                    probe=task.probe,
                    fraction=task.fraction,
                    repair=task.repair,
                )
            )
        if occurrence.identity in self.selected and self.selected[occurrence.identity] not in keys:
            message = f"unknown fixed offer for {occurrence.identity.path!r}"
            raise LayoutInvariantError(message)

    def _partition(self, task: _Assignment[RenderTargetT, ContextT, PayloadT]) -> None:
        """Turn a complete assignment into partition probes, starting from each region's required cuts."""
        self.context.work.consume(len(task.choices))
        choices = {offer.occurrence.identity: offer for offer, _ in task.choices}
        if self.selected.keys() - choices.keys():
            return
        candidate = _candidate(self.root, choices, self.context)
        regions = _region_trees(fragment_tree(candidate, self.context), self.context)
        self.seen_regions.update(node.candidate.offer.occurrence.identity for node in regions)
        if self.fixed_cuts.keys() - {node.candidate.offer.occurrence.identity for node in regions}:
            return
        if self.retained:
            # Partitions cannot improve representation preferences or remove their
            # effects. Grant every other quality axis its best possible value; only
            # strict dominance is safe because ties still use the final partition key.
            self.context.work.consume(len(task.choices) + len(self.retained))
            optimistic = AllocationQuality(
                (),
                _preferences(
                    candidate,
                    {offer.occurrence.identity: rank for offer, rank in task.choices},
                    self.policy,
                    self.context,
                ),
                frozenset(effect for offer, _ in task.choices for effect in offer.effects),
                (),
                0,
                (),
            )
            if any(compare_quality(previous, optimistic) == -1 for previous, _ in self.retained):
                return
        initial: list[tuple[int, ...]] = []
        for node in regions:
            contract = node.candidate.offer.region
            assert contract is not None
            required = contract.required_ends or (
                tuple(range(contract.per, len(node.units), contract.per)) if contract.per else ()
            )
            required = tuple(end for end in required if end < len(node.units))
            initial.append(self.fixed_cuts.get(node.candidate.offer.occurrence.identity, (*required, len(node.units))))
        cuts = tuple(initial)
        assignment = next(self.serials)
        if self.partition_hint:
            hinted: list[tuple[int, ...]] = []
            for node, required in zip(regions, cuts, strict=True):
                identity = node.candidate.offer.occurrence.identity
                ends = self.partition_hint.get(identity, required)
                self.context.work.consume(1 + len(ends) + len(node.legal_ends) + len(required))
                legal = set(node.legal_ends) | {len(node.units)}
                if (
                    ends != tuple(sorted(set(ends)))
                    or not ends
                    or ends[-1] != len(node.units)
                    or not set(ends) <= legal
                    or not set(required) <= set(ends)
                    or (identity in self.fixed_cuts and ends != required)
                ):
                    ends = required
                hinted.append(ends)
            self._push(_Partitions(assignment, candidate, task.choices, regions, tuple(hinted), probe=True))
        self._push(
            _Partitions(assignment, candidate, task.choices, regions, cuts, probe=True, repair=task.repair is not None)
        )
        # Try preserving every sibling while splitting just one region. Balanced
        # probes alone can miss a small feasible family when both pagers' chrome
        # cannot coexist. This only changes search order, not admissible cuts.
        if len(regions) > 1:
            for index, node in enumerate(regions):
                if node.candidate.offer.occurrence.identity not in self.fixed_cuts and len(node.legal_ends) <= 64:
                    unilateral = (*cuts[:index], node.legal_ends or (0,), *cuts[index + 1 :])
                    self._push(_Partitions(assignment, candidate, task.choices, regions, unilateral, probe=True))

    def _certify(self, task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> tuple[bool, bool]:
        """Certify a partition and retain its measured result when feasible."""
        self.context.work.consume()
        family = allocate_family(task.candidate, task.regions, task.cuts, self.context)
        region_map = {node.candidate.offer.occurrence.identity: node for node in task.regions}
        certificate = self.certifier.certify(family, region_map)
        if isinstance(certificate, FamilyRejection):
            self.violations.extend(certificate.violations)
            self.unsupported.update(
                violation.occurrence
                for violation in certificate.violations
                if isinstance(violation, UnsupportedCapability)
            )
            return False, False
        satisfied = self._retain(
            task, family, region_map, certificate.peaks, certificate.break_shortfalls, certificate.pages
        )
        return True, satisfied

    def _retain(
        self,
        task: _Partitions[RenderTargetT, ContextT, PayloadT],
        family: PageFamily[RenderTargetT, ContextT, PayloadT],
        region_map: Mapping[OccurrenceId, FragmentTree[RenderTargetT, ContextT, PayloadT]],
        peaks: Mapping[OccurrenceId, ResourceCost],
        page_violations: Mapping[tuple[OccurrenceId, int], int],
        pages: tuple[CertifiedPage[OutputT], ...],
    ) -> bool:
        """Score a feasible allocation and keep it unless a retained one is at least as good.

        Returns:
            Whether the allocation meets every soft goal and break.
        """
        self.context.work.consume(1 + len(task.choices) + len(region_map) + len(self.policy) + len(self.retained))
        ranks = {offer.occurrence.identity: rank for offer, rank in task.choices}
        preferences = _preferences(task.candidate, ranks, self.policy, self.context)
        minimums: list[int] = []
        for axis in Axis:
            self.context.work.consume(1 + sum(len(value.goals) for value in self.policy.values()))
            deficits = [
                max(0, goal.minimum - peaks.get(identity, EMPTY_COST).get(axis)) if identity in region_map else 0
                for identity, region_policy in self.policy.items()
                for goal in region_policy.goals
                if goal.axis == axis and not goal.hard
            ]
            minimums.extend(sorted(deficits, reverse=True))
        effects: frozenset[FidelityEffect] = frozenset(effect for offer, _ in task.choices for effect in offer.effects)
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
            for previous, _ in self.retained
        ):
            self.context.work.consume(len(self.retained) + len(pages) + len(region_map) + len(task.choices))
            self.retained = [
                (previous, allocation)
                for previous, allocation in self.retained
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
            self.context.work.consume(sum(1 + len(page.coordinates) for page in pages))
            allocation = AllocationSnapshot.certify(
                family,
                tuple(pages),
                tuple(
                    sorted(
                        ((offer.occurrence.identity, offer.key) for offer, _ in task.choices),
                        key=lambda item: item[0].path,
                    )
                ),
            )
            self.retained.append((quality, allocation))
        return satisfied

    def _push_repair(self, task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> None:
        # Try one structural step at a time before the broad compact
        # probes. This finds useful fits under component pressure without
        # shortening every independent text ladder. Failed probes never prune.
        self.repair_started = True
        steps: list[tuple[int, int, tuple[str, ...], RealizationOffer[RenderTargetT, ContextT, PayloadT]]] = []
        for current, rank in task.choices:
            if current.occurrence.identity in self.selected:
                continue
            discovery = self.discoveries[id(current.occurrence)]
            domain = discovery.offers if isinstance(discovery, OfferDiscovery) else discovery
            self.context.work.consume(len(domain))
            domain_ranks = dense_ranks(tuple(offer.preference for offer in domain))
            better = [
                (offer_rank, offer)
                for offer, offer_rank in zip(domain, domain_ranks, strict=True)
                if offer_rank > rank
                and (not self.context.strict or not offer.effects)
                and (not offer.effects or current.children or offer.children)
            ]
            if better:
                next_rank, next_offer = min(better, key=lambda entry: (entry[0], entry[1].key))
                steps.append(
                    (current.preference_priority or 0, next_rank, current.occurrence.identity.path, next_offer)
                )
        if steps:
            _, _, _, changed = min(steps, key=lambda entry: entry[:3])
            self.context.work.consume(len(task.choices))
            hints = {offer.occurrence.identity: offer.key for offer, _ in task.choices}
            hints[changed.occurrence.identity] = changed.key
            self._push(_Assignment((self.root,), probe=True, repair=hints))

    def _push_degradation_probes(self, task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> None:
        # Find a compact certificate before enumerating combinations of
        # independent ladders. This changes work order only: ordinary
        # assignments remain queued and still establish optimality.
        self.degradation_probed = True
        # Intermediate rungs avoid an empty incumbent when several prose
        # blocks must share space. This samples work order, never feasibility.
        self.context.work.consume(len(task.choices))
        adjustable = 0
        for current, _ in task.choices:
            discovery = self.discoveries[id(current.occurrence)]
            domain = discovery.offers if isinstance(discovery, OfferDiscovery) else discovery
            self.context.work.consume(len(domain))
            if self.choice_counts.get(current.occurrence.identity, 0) > 1 and (
                not task.regions or any(offer.effects for offer in domain)
            ):
                adjustable += 1
        if adjustable:
            for fraction in dict.fromkeys((1.0, 1 - 1 / max(2, adjustable), 0.5, 0.75, 0.875, 0.9375)):
                self._push(_Assignment((self.root,), probe=True, fraction=fraction))

    def _refine_probe(self, task: _Partitions[RenderTargetT, ContextT, PayloadT]) -> None:
        # Balanced refinement is only a work-order heuristic. Every legal
        # cut is still enqueued below; no fit assumption prunes that domain.
        refined: list[tuple[int, ...]] = []
        for node, ends in zip(task.regions, task.cuts, strict=True):
            self.context.work.consume(1 + len(node.legal_ends) + len(ends))
            available = [end for end in node.legal_ends if end not in ends]
            if node.candidate.offer.occurrence.identity in self.fixed_cuts or not available:
                refined.append(ends)
            else:
                start, end = max(zip((0, *ends[:-1]), ends, strict=True), key=lambda pair: pair[1] - pair[0])
                middle = min(available, key=lambda cut: (abs(cut - (start + end) / 2), cut))
                refined.append(tuple(sorted((*ends, middle))))
        if tuple(refined) != task.cuts:
            self._push(replace(task, cuts=tuple(refined), probe=True))
        # Certify the next probe before spending the allowance on its many
        # alternatives. Deferred expansion still explores every legal cut.
        self._push(replace(task, probe=False, expand_only=True))

    def _result(self) -> JointResult[OutputT]:
        """Return the best retained allocation, or raise why the search found none."""

        if not self.exhausted and (
            self.selected.keys() - self.seen_occurrences
            or (self.policy.keys() | self.fixed_cuts.keys()) - self.seen_regions
        ):
            message = "joint search options contain unknown occurrences"
            raise LayoutInvariantError(message)
        completion = (
            SearchCompletion.EXHAUSTED
            if self.exhausted
            else SearchCompletion.UNSUPPORTED
            if self.unsupported
            else SearchCompletion.COMPLETE
        )
        diagnostics = SearchDiagnostics(
            completion,
            not self.exhausted and not self.unsupported,
            self.context.work.spent,
            self.context.work.limit,
            self.generated,
            self.context.work.target_evaluations - self.initial_evaluations,
            self.certifier.certified_combinations,
            tuple(self.violations),
            tuple(sorted(self.unsupported, key=lambda identity: identity.path)),
        )
        if not self.retained:
            if self.exhausted:
                raise JointExhaustedError(diagnostics)
            if self.unsupported:
                raise JointUnsupportedError(diagnostics)
            raise JointInfeasibleError(diagnostics)
        _, allocation = min(self.retained, key=lambda entry: entry[0].tie)
        return JointResult(
            allocation, diagnostics, tuple(entry[0] for entry in sorted(self.retained, key=lambda entry: entry[0].tie))
        )


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
) -> JointResult[OutputT]:
    """Certify a nondominated allocation without publishing bindings or state.

    Fixed selections/partitions constrain the domain for reproducible tests. The
    finalizer runs within the allowance, before an allocation can become an
    incumbent. All reachable outputs are built, so selection and bounded search
    are independent of the requested cursor. Only terminal status is added after
    exhaustion.
    """
    return _JointSearch(
        root,
        rules,
        context,
        finalize=finalize,
        selections=selections,
        policies=policies,
        partitions=partitions,
        partition_hint=partition_hint,
    ).run()

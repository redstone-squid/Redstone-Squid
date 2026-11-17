"""Bounded joint search over representations and stable legal page partitions.

The queue retains partial assignments and exact partition states. Fit estimates
only order probes; rejection uses complete, contextual evaluations. An assignment
can be skipped when a certified family beats even its best possible quality:
zero shortfalls and no page turns. Feasible families with satisfied soft goals
also need no partition refinements, which necessarily add page turns.
Opaque payloads and scalar resource envelopes never establish dominance.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, replace
from heapq import heappop, heappush
from itertools import count
from types import MappingProxyType

from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.planning.allocation import AllocationSnapshot, PageFamily, allocate_family
from squid_ui.planning.allocation_policy import (
    AllocationQuality,
    RegionPolicy,
    SearchCompletion,
    SearchDiagnostics,
    compare_quality,
)
from squid_ui.planning.allocation_scoring import preference_tree, score_family
from squid_ui.planning.certification import FamilyCertificate, FamilyCertifier, FamilyRejection
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateComposition,
    CandidateViolation,
    OccurrenceId,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.fragments import FragmentTree, fragment_tree
from squid_ui.planning.offer_explorer import OfferExplorer
from squid_ui.planning.resources import EMPTY_COST
from squid_ui.planning.search_tasks import AssignmentTask, PartitionTask


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


class _JointSearch[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT]:
    """One run of `search_composition`, with the queue and bookkeeping its phases share.

    The queue holds two kinds of task. An `AssignmentTask` chooses one offer per occurrence, depth first;
    once every occurrence has an offer, it becomes `PartitionTask` tasks that try region page cuts.
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
        self.context = context
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
                AssignmentTask[RenderTargetT, ContextT, PayloadT] | PartitionTask[RenderTargetT, ContextT, PayloadT],
            ]
        ] = []
        self.retained: list[tuple[AllocationQuality, AllocationSnapshot[OutputT]]] = []
        self.certifier = FamilyCertifier(rules, context, self.policy, finalize)
        self.unsupported: set[OccurrenceId] = set()
        self.violations: list[CandidateViolation] = []
        self.offers = OfferExplorer(root, rules, context, self.selected, self.violations, self.unsupported)
        self.generated = 0
        self.initial_evaluations = self.context.work.target_evaluations
        self.exhausted = False
        self.seen_partitions: set[tuple[int, tuple[tuple[int, ...], ...], bool]] = set()
        self.seen_regions: set[OccurrenceId] = set()

    def _candidate(
        self,
        occurrence: SemanticOccurrence[RenderTargetT, ContextT],
        choices: Mapping[OccurrenceId, RealizationOffer[RenderTargetT, ContextT, PayloadT]],
    ) -> CandidateComposition[RenderTargetT, ContextT, PayloadT]:
        self.context.work.consume()
        offer = choices[occurrence.identity]
        return CandidateComposition(offer, tuple(self._candidate(child, choices) for child in offer.children))

    def _region_trees(
        self, root: FragmentTree[RenderTargetT, ContextT, PayloadT]
    ) -> tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]:
        found: list[FragmentTree[RenderTargetT, ContextT, PayloadT]] = []
        pending = [root]
        while pending:
            self.context.work.consume()
            part = pending.pop()
            if part.candidate.offer.region is not None:
                found.append(part)
            pending.extend(reversed(part.children))
        return tuple(found)

    def run(self) -> JointResult[OutputT]:
        try:
            self._push(AssignmentTask((self.root,)))
            while self.queue:
                self.context.work.consume()
                _, _, _, _, _, task = heappop(self.queue)
                if isinstance(task, AssignmentTask):
                    if task.pending:
                        self._enqueue(self.offers.branch(task))
                    else:
                        self._partition(task)
                    continue
                if task.expand_only:
                    self._expand(task)
                    continue
                feasible, satisfied = self._certify(task)
                if not feasible and (task.repair or not self.offers.repair_started):
                    self._enqueue(self.offers.repair(task))
                if feasible and satisfied:
                    continue
                if not feasible and not self.offers.degradation_probed:
                    self._enqueue(self.offers.degradation_probes(task))
                if task.probe:
                    self._refine_probe(task)
                else:
                    self._expand(task)
        except LayoutSearchExhaustedError:
            self.exhausted = True
        return self._result()

    def _enqueue(self, tasks: Iterator[AssignmentTask[RenderTargetT, ContextT, PayloadT]]) -> None:
        for task in tasks:
            self._push(task)

    def _push(
        self,
        task: AssignmentTask[RenderTargetT, ContextT, PayloadT] | PartitionTask[RenderTargetT, ContextT, PayloadT],
    ) -> None:
        self.context.work.consume(1 + len(task.choices))
        if isinstance(task, PartitionTask):
            identity = (task.assignment, task.cuts, task.expand_only)
            if identity in self.seen_partitions:
                return
            self.seen_partitions.add(identity)
        self.generated += 1
        ranks = tuple(sorted((rank for _, rank in task.choices if rank), reverse=True))
        turns = sum(len(cuts) - 1 for cuts in task.cuts) if isinstance(task, PartitionTask) else 0
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

    def _expand(self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]) -> None:
        """Queue every partition that adds one more legal cut to one region."""
        for index, node in enumerate(task.regions):
            if node.candidate.offer.occurrence.identity in self.fixed_cuts:
                continue
            for end in node.legal_ends[:-1]:
                self.context.work.consume()
                if end not in task.cuts[index]:
                    changed = (*task.cuts[:index], tuple(sorted((*task.cuts[index], end))), *task.cuts[index + 1 :])
                    self._push(replace(task, cuts=changed, probe=False, expand_only=False))

    def _partition(self, task: AssignmentTask[RenderTargetT, ContextT, PayloadT]) -> None:
        """Turn a complete assignment into partition probes, starting from each region's required cuts."""
        self.context.work.consume(len(task.choices))
        choices = {offer.occurrence.identity: offer for offer, _ in task.choices}
        if self.selected.keys() - choices.keys():
            return
        candidate = self._candidate(self.root, choices)
        regions = self._region_trees(fragment_tree(candidate, self.context))
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
                preference_tree(
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
            self._push(PartitionTask(assignment, candidate, task.choices, regions, tuple(hinted), probe=True))
        self._push(
            PartitionTask(
                assignment, candidate, task.choices, regions, cuts, probe=True, repair=task.repair is not None
            )
        )
        # Try preserving every sibling while splitting just one region. Balanced
        # probes alone can miss a small feasible family when both pagers' chrome
        # cannot coexist. This only changes search order, not admissible cuts.
        if len(regions) > 1:
            for index, node in enumerate(regions):
                if node.candidate.offer.occurrence.identity not in self.fixed_cuts and len(node.legal_ends) <= 64:
                    unilateral = (*cuts[:index], node.legal_ends or (0,), *cuts[index + 1 :])
                    self._push(PartitionTask(assignment, candidate, task.choices, regions, unilateral, probe=True))

    def _certify(self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]) -> tuple[bool, bool]:
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
        satisfied = self._retain(task, family, region_map, certificate)
        return True, satisfied

    def _retain(
        self,
        task: PartitionTask[RenderTargetT, ContextT, PayloadT],
        family: PageFamily[RenderTargetT, ContextT, PayloadT],
        region_map: Mapping[OccurrenceId, FragmentTree[RenderTargetT, ContextT, PayloadT]],
        certificate: FamilyCertificate[OutputT],
    ) -> bool:
        """Score a feasible allocation and keep it unless a retained one is at least as good.

        Returns:
            Whether the allocation meets every soft goal and break.
        """
        self.context.work.consume(1 + len(task.choices) + len(region_map) + len(self.policy) + len(self.retained))
        quality, satisfied = score_family(family, task.choices, region_map, certificate, self.policy, self.context)
        if not any(
            compare_quality(previous, quality) == -1
            or (compare_quality(previous, quality) == 0 and previous.tie <= quality.tie)
            for previous, _ in self.retained
        ):
            self.context.work.consume(len(self.retained) + len(certificate.pages) + len(region_map) + len(task.choices))
            self.retained = [
                (previous, allocation)
                for previous, allocation in self.retained
                if compare_quality(quality, previous) not in (-1, 0)
            ]
            family = replace(
                family,
                regions=MappingProxyType(
                    {
                        identity: replace(region, allocation=certificate.peaks.get(identity, EMPTY_COST))
                        for identity, region in family.regions.items()
                    }
                ),
            )
            self.context.work.consume(sum(1 + len(page.coordinates) for page in certificate.pages))
            allocation = AllocationSnapshot.certify(
                family,
                certificate.pages,
                tuple(
                    sorted(
                        ((offer.occurrence.identity, offer.key) for offer, _ in task.choices),
                        key=lambda item: item[0].path,
                    )
                ),
            )
            self.retained.append((quality, allocation))
        return satisfied

    def _refine_probe(self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]) -> None:
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

        exhausted = self.exhausted or self.offers.exhausted
        if not exhausted and (
            self.selected.keys() - self.offers.seen_occurrences
            or (self.policy.keys() | self.fixed_cuts.keys()) - self.seen_regions
        ):
            message = "joint search options contain unknown occurrences"
            raise LayoutInvariantError(message)
        completion = (
            SearchCompletion.EXHAUSTED
            if exhausted
            else SearchCompletion.UNSUPPORTED
            if self.unsupported
            else SearchCompletion.COMPLETE
        )
        diagnostics = SearchDiagnostics(
            completion,
            not exhausted and not self.unsupported,
            self.context.work.spent,
            self.context.work.limit,
            self.generated,
            self.context.work.target_evaluations - self.initial_evaluations,
            self.certifier.certified_combinations,
            tuple(self.violations),
            tuple(sorted(self.unsupported, key=lambda identity: identity.path)),
        )
        if not self.retained:
            if exhausted:
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

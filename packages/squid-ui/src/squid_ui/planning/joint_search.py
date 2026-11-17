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
from squid_ui.planning.allocation_scoring import score_family
from squid_ui.planning.certification import FamilyCertificate, FamilyCertifier, FamilyRejection
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateViolation,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.fragments import FragmentTree
from squid_ui.planning.offer_explorer import OfferExplorer
from squid_ui.planning.partition_explorer import PartitionExplorer
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
        self.selected = selections or {}
        self.policy = policies or {}
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
        self.partitions = PartitionExplorer(root, context, self.selected, self.policy, partitions or {}, partition_hint)
        self.generated = 0
        self.initial_evaluations = self.context.work.target_evaluations
        self.exhausted = False

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
                        self._enqueue(self.partitions.seed(task, tuple(quality for quality, _ in self.retained)))
                    continue
                if task.expand_only:
                    self._enqueue(self.partitions.expand(task))
                    continue
                feasible, satisfied = self._certify(task)
                if not feasible and (task.repair or not self.offers.repair_started):
                    self._enqueue(self.offers.repair(task))
                if feasible and satisfied:
                    continue
                if not feasible and not self.offers.degradation_probed:
                    self._enqueue(self.offers.degradation_probes(task))
                if task.probe:
                    self._enqueue(self.partitions.refine_probe(task))
                else:
                    self._enqueue(self.partitions.expand(task))
        except LayoutSearchExhaustedError:
            self.exhausted = True
        return self._result()

    def _enqueue(
        self,
        tasks: Iterator[
            AssignmentTask[RenderTargetT, ContextT, PayloadT] | PartitionTask[RenderTargetT, ContextT, PayloadT]
        ],
    ) -> None:
        for task in tasks:
            self._push(task)

    def _push(
        self,
        task: AssignmentTask[RenderTargetT, ContextT, PayloadT] | PartitionTask[RenderTargetT, ContextT, PayloadT],
    ) -> None:
        self.context.work.consume(1 + len(task.choices))
        if isinstance(task, PartitionTask) and not self.partitions.mark_seen(task):
            return
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

    def _result(self) -> JointResult[OutputT]:
        """Return the best retained allocation, or raise why the search found none."""

        exhausted = self.exhausted or self.offers.exhausted
        if not exhausted and (
            self.selected.keys() - self.offers.seen_occurrences
            or (self.policy.keys() | self.partitions.fixed_cuts.keys()) - self.partitions.seen_regions
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

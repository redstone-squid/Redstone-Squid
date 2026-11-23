"""Bounded joint search over representations and stable legal page partitions.

The queue retains partial assignments and exact partition states. Intrinsic offer
failures and hard page bounds guide the search before contextual evaluation. An assignment
can be skipped when a certified family beats even its best possible quality:
zero shortfalls and no page turns. Feasible families with satisfied soft goals
also need no partition refinements, which necessarily add page turns.
Opaque payloads and scalar resource envelopes never establish dominance.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import count

from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.planning.allocation import AllocationSnapshot, PageFamily
from squid_ui.planning.allocation_frontier import AllocationFrontier
from squid_ui.planning.allocation_policy import (
    AllocationQuality,
    RegionPolicy,
    SearchCompletion,
    SearchDiagnostics,
)
from squid_ui.planning.certification import FamilyCertifier, FamilyRejection
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateViolation,
    CapacityExceeded,
    LocalCapacityExceeded,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.offer_explorer import AssignmentTask, OfferExplorer, Pending
from squid_ui.planning.partition_explorer import PartitionExplorer, PartitionTask


class JointInfeasibleError(UnsolvableLayoutError):
    def __init__(self, diagnostics: SearchDiagnostics) -> None:
        self.diagnostics = diagnostics
        limits = tuple(
            dict.fromkeys(
                (violation.limit, violation.used, violation.capacity)
                for violation in diagnostics.violations
                if isinstance(violation, LocalCapacityExceeded)
            )
        )[:8]
        context: dict[str, object] = {}
        if limits:
            context["local_limits"] = [
                {"limit": limit, "used": used, "capacity": capacity} for limit, used, capacity in limits
            ]
        if diagnostics.unavoidable_overflow:
            context["minimum_costs"] = [
                {"axis": limit.axis, "minimum": limit.used, "capacity": limit.capacity}
                for limit in diagnostics.unavoidable_overflow
            ]
        super().__init__(
            "no declared joint allocation is feasible",
            context=context or None,
        )


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


class _SearchCoordinator[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT, OutputT]:
    """Schedule one joint search across offer, partition, and certification work.

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
        excluded: tuple[tuple[tuple[OccurrenceId, str], ...], ...],
    ) -> None:
        self.root = root
        self.context = context
        self.selected = selections or {}
        self.policy = policies or {}
        self.serials = count()
        self.queue: list[
            tuple[
                int,
                tuple[tuple[int, int], ...],
                int,
                int,
                int,
                AssignmentTask[RenderTargetT, ContextT, PayloadT] | PartitionTask[RenderTargetT, ContextT, PayloadT],
            ]
        ] = []
        self.frontier = AllocationFrontier[RenderTargetT, ContextT, PayloadT, OutputT](context, self.policy)
        self.certifier = FamilyCertifier(rules, context, self.policy, finalize)
        self.unsupported: set[OccurrenceId] = set()
        self.violations: list[CandidateViolation] = []
        self.offers = OfferExplorer(root, rules, context, self.selected, self.violations, self.unsupported, excluded)
        self.partitions = PartitionExplorer(
            root, context, self.selected, self.policy, partitions or {}, partition_hint, self.violations
        )
        self.generated = 0
        self.initial_evaluations = self.context.work.target_evaluations
        self.exhausted = False
        self.unavoidable_overflow: tuple[CapacityExceeded, ...] = ()

    def run(self) -> JointResult[OutputT]:
        try:
            self._push(AssignmentTask(Pending((self.root,))))
            while self.queue:
                self.context.work.consume()
                _, _, _, _, _, task = heappop(self.queue)
                if isinstance(task, AssignmentTask):
                    if task.pending:
                        self._enqueue(self.offers.branch(task))
                    else:
                        self.context.work.consume(len(task.history))
                        self._enqueue(
                            self.partitions.seed(
                                tuple(reversed(tuple(task.history))),
                                self.frontier.qualities(),
                                repair=task.repair is not None,
                            )
                        )
                    continue
                if (oversized := self.partitions.oversized_page(task)) is not None:
                    self._enqueue(self.partitions.refine_oversized(task, oversized))
                    continue
                if task.expand_only:
                    self._enqueue(self.partitions.expand(task))
                    continue
                feasible, satisfied = self._certify(task)
                if self.unavoidable_overflow:
                    break
                if not feasible and (task.repair or not self.offers.repair_started):
                    self._enqueue(self.offers.repair(task.choices))
                if feasible and satisfied:
                    continue
                if not feasible and not self.offers.degradation_probed:
                    self._enqueue(self.offers.degradation_probes(task.choices, has_regions=bool(task.regions)))
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
        if isinstance(task, AssignmentTask):
            self.context.work.consume()
            depth = len(task.history)
            ranks = task.ranks
        else:
            depth = len(task.choices)
            self.context.work.consume(1 + depth)
            if not self.partitions.mark_seen(task):
                return
            counts = Counter(rank for _, rank in task.choices if rank)
            ranks = tuple(sorted(counts.items(), reverse=True))
        self.generated += 1
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
                -depth,
                next(self.serials),
                task,
            ),
        )

    def _certify(self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]) -> tuple[bool, bool]:
        """Certify a partition and retain its measured result when feasible."""
        self.context.work.consume()
        family = PageFamily.from_cuts(task.candidate, task.regions, task.cuts, self.context)
        region_map = {node.candidate.offer.occurrence.identity: node for node in task.regions}
        certificate = self.certifier.certify(family, region_map)
        if isinstance(certificate, FamilyRejection):
            self.violations.extend(certificate.violations)
            self.unsupported.update(
                violation.occurrence
                for violation in certificate.violations
                if isinstance(violation, UnsupportedCapability)
            )
            self.unavoidable_overflow = self.offers.check_root_bound(certificate.violations)
            return False, False
        satisfied = self.frontier.consider(family, task.choices, region_map, certificate)
        return True, satisfied

    def _result(self) -> JointResult[OutputT]:
        """Return the best retained allocation, or raise why the search found none."""

        exhausted = (self.exhausted or self.offers.exhausted) and not self.unavoidable_overflow
        # A pruned offer can prevent discovery of later
        # occurrences. The infeasibility is known even if option validation is incomplete.
        if (
            not exhausted
            and not self.unavoidable_overflow
            and (self.frontier.has_result or not self.offers.pruned)
            and (
                self.selected.keys() - self.offers.seen_occurrences
                or (self.policy.keys() | self.partitions.fixed_cuts.keys()) - self.partitions.seen_regions
            )
        ):
            message = "joint search options contain unknown occurrences"
            raise LayoutInvariantError(message)
        completion = (
            SearchCompletion.EXHAUSTED
            if exhausted
            else SearchCompletion.UNSUPPORTED
            if self.unsupported and not self.unavoidable_overflow
            else SearchCompletion.COMPLETE
        )
        diagnostics = SearchDiagnostics(
            completion,
            completion == SearchCompletion.COMPLETE,
            self.context.work.spent,
            self.context.work.limit,
            self.generated,
            self.context.work.target_evaluations - self.initial_evaluations,
            self.certifier.certified_combinations,
            tuple(self.violations),
            tuple(sorted(self.unsupported, key=lambda identity: identity.path)),
            self.unavoidable_overflow,
        )
        if not self.frontier.has_result:
            if exhausted:
                raise JointExhaustedError(diagnostics)
            if self.unsupported and not self.unavoidable_overflow:
                raise JointUnsupportedError(diagnostics)
            raise JointInfeasibleError(diagnostics)
        allocation, qualities = self.frontier.best()
        return JointResult(allocation, diagnostics, qualities)


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
    excluded: tuple[tuple[tuple[OccurrenceId, str], ...], ...] = (),
) -> JointResult[OutputT]:
    """Certify a nondominated allocation without publishing bindings or state.

    Fixed selections/partitions constrain the domain for reproducible tests. The
    finalizer runs within the allowance, before an allocation can become an
    incumbent. All reachable outputs are built, so selection and bounded search
    are independent of the requested cursor. Only terminal status is added after
    exhaustion.
    """
    return _SearchCoordinator(
        root,
        rules,
        context,
        finalize=finalize,
        selections=selections,
        policies=policies,
        partitions=partitions,
        partition_hint=partition_hint,
        excluded=excluded,
    ).run()

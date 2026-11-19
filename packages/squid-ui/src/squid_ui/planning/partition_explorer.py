"""Generate legal page cuts for complete representation assignments.

One explorer belongs to one joint search. It constructs the candidate tree,
tracks the regions reached by assignments, and yields initial and refined
partition tasks. The coordinator decides when to certify or enqueue them.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from itertools import count
from math import ceil

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.allocation_policy import AllocationQuality, RegionPolicy, compare_quality
from squid_ui.planning.allocation_scoring import preference_tree
from squid_ui.planning.composition import (
    CandidateComposition,
    CandidateViolation,
    LocalCapacityExceeded,
    OccurrenceId,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
)
from squid_ui.planning.fragments import FragmentTree, fragment_tree


@dataclass(frozen=True, slots=True)
class PartitionTask[RenderTargetT, ContextT, PayloadT]:
    assignment: int
    candidate: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...]
    regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...]
    cuts: tuple[tuple[int, ...], ...]
    probe: bool = False
    expand_only: bool = False
    repair: bool = False


class PartitionExplorer[RenderTargetT, ContextT, PayloadT]:
    """Yield partition tasks without owning the queue or certifying pages."""

    def __init__(
        self,
        root: SemanticOccurrence[RenderTargetT, ContextT],
        context: PlanningContext,
        selections: Mapping[OccurrenceId, str],
        policies: Mapping[OccurrenceId, RegionPolicy],
        fixed_cuts: Mapping[OccurrenceId, tuple[int, ...]],
        partition_hint: Mapping[OccurrenceId, tuple[int, ...]] | None,
        violations: list[CandidateViolation],
    ) -> None:
        self.root = root
        self.context = context
        self.selected = selections
        self.policy = policies
        self.fixed_cuts = fixed_cuts
        self.partition_hint = partition_hint
        self.violations = violations
        self.seen_regions: set[OccurrenceId] = set()
        self.seen_partitions: set[tuple[int, tuple[tuple[int, ...], ...], bool]] = set()
        self.assignment_ids = count()

    def mark_seen(self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]) -> bool:
        """Reserve a partition state once, including deferred expansion tasks."""
        identity = (task.assignment, task.cuts, task.expand_only)
        if identity in self.seen_partitions:
            return False
        self.seen_partitions.add(identity)
        return True

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

    def expand(
        self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]
    ) -> Iterator[PartitionTask[RenderTargetT, ContextT, PayloadT]]:
        """Queue every partition that adds one more legal cut to one region."""
        for index, node in enumerate(task.regions):
            if node.candidate.offer.occurrence.identity in self.fixed_cuts:
                continue
            for end in node.legal_ends[:-1]:
                self.context.work.consume()
                if end not in task.cuts[index]:
                    changed = (*task.cuts[:index], tuple(sorted((*task.cuts[index], end))), *task.cuts[index + 1 :])
                    yield replace(task, cuts=changed, probe=False, expand_only=False)

    def oversized_page(
        self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]
    ) -> tuple[int, int, int, int] | None:
        """Find the first page whose next refinement must insert a cut."""
        # A local certificate can skip offscreen pages, including nested regions.
        if self.context.local_view:
            return None
        for index, (node, ends) in enumerate(zip(task.regions, task.cuts, strict=True)):
            contract = node.candidate.offer.region
            assert contract is not None
            if (limit := contract.max_units) is None:
                continue
            self.context.work.consume(len(ends))
            for start, end in zip((0, *ends[:-1]), ends, strict=True):
                if end - start > limit:
                    return index, start, end, limit
        return None

    def refine_oversized(
        self, task: PartitionTask[RenderTargetT, ContextT, PayloadT], oversized: tuple[int, int, int, int]
    ) -> Iterator[PartitionTask[RenderTargetT, ContextT, PayloadT]]:
        """Branch only on cuts that can help resolve the first hard page bound."""
        index, start, end, limit = oversized
        node = task.regions[index]
        identity = node.candidate.offer.occurrence.identity
        self.context.work.consume(len(node.legal_ends))
        available = [cut for cut in node.legal_ends if start < cut < end] if identity not in self.fixed_cuts else []
        if not available:
            self.violations.append(LocalCapacityExceeded(identity, "page.units", end - start, limit))
            return
        if task.probe and not task.expand_only:
            # Probe an even split over the minimum number of pages, then
            # explore other cuts if needed. No legal refinement is lost.
            goal = start + (end - start) / ceil((end - start) / limit)
            available = [min(available, key=lambda cut: (abs(cut - goal), cut))]
            yield replace(task, probe=False, expand_only=True)
        for cut in available:
            ends = tuple(sorted((*task.cuts[index], cut)))
            yield replace(
                task,
                cuts=(*task.cuts[:index], ends, *task.cuts[index + 1 :]),
                expand_only=False,
            )

    def seed(
        self,
        choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...],
        retained: tuple[AllocationQuality, ...],
        *,
        repair: bool,
    ) -> Iterator[PartitionTask[RenderTargetT, ContextT, PayloadT]]:
        """Turn a complete assignment into partition probes, starting from each region's required cuts."""
        self.context.work.consume(len(choices))
        selected = {offer.occurrence.identity: offer for offer, _ in choices}
        if self.selected.keys() - selected.keys():
            return
        candidate = self._candidate(self.root, selected)
        regions = self._region_trees(fragment_tree(candidate, self.context))
        self.seen_regions.update(node.candidate.offer.occurrence.identity for node in regions)
        if self.fixed_cuts.keys() - {node.candidate.offer.occurrence.identity for node in regions}:
            return
        if retained:
            # Partitions cannot improve representation preferences or remove their
            # effects. Grant every other quality axis its best possible value; only
            # strict dominance is safe because ties still use the final partition key.
            self.context.work.consume(len(choices) + len(retained))
            optimistic = AllocationQuality(
                (),
                preference_tree(
                    candidate,
                    {offer.occurrence.identity: rank for offer, rank in choices},
                    self.policy,
                    self.context,
                ),
                frozenset(effect for offer, _ in choices for effect in offer.effects),
                (),
                0,
                (),
            )
            if any(compare_quality(previous, optimistic) == -1 for previous in retained):
                return
        initial: list[tuple[int, ...]] = []
        for node in regions:
            contract = node.candidate.offer.region
            assert contract is not None
            required = contract.required_ends or (
                tuple(range(contract.per, len(node.units), contract.per)) if contract.per else ()
            )
            required = tuple(end for end in required if end < len(node.units))
            fixed = self.fixed_cuts.get(node.candidate.offer.occurrence.identity)
            ends = (*required, len(node.units)) if fixed is None else fixed
            if contract.max_units is not None and (fixed is not None or required):
                self.context.work.consume(len(ends) + len(node.legal_ends))
                if (
                    not ends
                    or ends[-1] != len(node.units)
                    or ends != tuple(sorted(set(ends)))
                    or not set(ends) <= (set(node.legal_ends) or {0})
                ):
                    message = "region cuts must cover every unit at legal boundaries"
                    raise LayoutInvariantError(message)
            initial.append(ends)
        cuts = tuple(initial)
        assignment = next(self.assignment_ids)
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
            yield PartitionTask(assignment, candidate, choices, regions, tuple(hinted), probe=True)
        yield PartitionTask(assignment, candidate, choices, regions, cuts, probe=True, repair=repair)
        # Try preserving every sibling while splitting just one region. Balanced
        # probes alone can miss a small feasible family when both pagers' chrome
        # cannot coexist. This only changes search order, not admissible cuts.
        if len(regions) > 1:
            for index, node in enumerate(regions):
                if node.candidate.offer.occurrence.identity not in self.fixed_cuts and len(node.legal_ends) <= 64:
                    unilateral = (*cuts[:index], node.legal_ends or (0,), *cuts[index + 1 :])
                    yield PartitionTask(assignment, candidate, choices, regions, unilateral, probe=True)

    def refine_probe(
        self, task: PartitionTask[RenderTargetT, ContextT, PayloadT]
    ) -> Iterator[PartitionTask[RenderTargetT, ContextT, PayloadT]]:
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
            yield replace(task, cuts=tuple(refined), probe=True)
        # Certify the next probe before spending the allowance on its many
        # alternatives. Deferred expansion still explores every legal cut.
        yield replace(task, probe=False, expand_only=True)

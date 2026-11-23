"""Explore representation offers and prioritize repairs after failed partitions.

One instance belongs to one joint search. Discovery results stay cached while
queued assignments refer to their occurrences, including partial discoveries
that mark the search exhausted without preventing a feasible certificate.
"""

from collections import defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property

from rpds import HashTrieMap, List

from squid_ui.errors import LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.planning.allocation_policy import dense_ranks
from squid_ui.planning.composition import (
    CandidateViolation,
    CapacityExceeded,
    DiscoveryCompletion,
    OccurrenceId,
    OfferDiscovery,
    PlanningContext,
    RealizationOffer,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
)
from squid_ui.planning.resources import EMPTY_COST, ResourceCost

type OfferChoice = tuple[OccurrenceId, str]


@dataclass(frozen=True, slots=True)
class Pending[T]:
    siblings: tuple[T, ...]
    index: int = 0
    tail: Pending[T] | None = None
    bound: bool = True

    def advance(self) -> Pending[T] | None:
        if self.index + 1 == len(self.siblings):
            return self.tail
        return Pending(self.siblings, self.index + 1, self.tail, self.bound)


@dataclass(frozen=True, slots=True)
class AssignmentTask[RenderTargetT, ContextT, PayloadT]:
    pending: Pending[SemanticOccurrence[RenderTargetT, ContextT]] | None
    history: List[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int]] = field(default_factory=List)
    selected: HashTrieMap[OccurrenceId, str] = field(default_factory=HashTrieMap)
    ranks: tuple[tuple[int, int], ...] = ()
    probe: bool = False
    fraction: float = 1.0
    repair: Mapping[OccurrenceId, str] | None = None
    minimum_cost: ResourceCost = EMPTY_COST


class OfferExplorer[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT]:
    """Discover choices and yield assignment tasks without owning the search queue."""

    def __init__(
        self,
        root: SemanticOccurrence[RenderTargetT, ContextT],
        rules: TargetRules[RenderTargetT, ContextT, PayloadT, ResourceT, BodyT, PreparationT],
        context: PlanningContext,
        selections: Mapping[OccurrenceId, str],
        violations: list[CandidateViolation],
        unsupported: set[OccurrenceId],
        excluded: tuple[tuple[OfferChoice, ...], ...],
    ) -> None:
        self.root = root
        self.rules = rules
        self.context = context
        self.selected = selections
        self.violations = violations
        self.unsupported = unsupported
        self.excluded = excluded
        self.seen_occurrences: set[OccurrenceId] = set()
        self.degradation_probed = False
        self.repair_started = False
        self.exhausted = False
        self.pruned = False
        self.explored: set[int] = set()
        self.minimums: dict[int, ResourceCost] = {}
        self.bounds_checked = False
        self.suffix_minimums: dict[tuple[int, int], ResourceCost] = {}
        self.bound_rejections: set[CapacityExceeded] = set()
        self.choice_counts: dict[OccurrenceId, int] = {}
        # Keep each occurrence alive while its identity keys a cached discovery.
        self.discoveries: dict[
            int,
            tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
            | OfferDiscovery[RenderTargetT, ContextT, PayloadT],
        ] = {}
        self.discovery_owners: dict[int, SemanticOccurrence[RenderTargetT, ContextT]] = {}

    @cached_property
    def exclusions_by_choice(self) -> Mapping[OfferChoice, list[frozenset[OfferChoice]]]:
        """Index each forbidden combination by any choice that could complete it."""
        indexed: dict[OfferChoice, list[frozenset[OfferChoice]]] = defaultdict(list)
        for pattern in self.excluded:
            self.context.work.consume(1 + len(pattern))
            choices = frozenset(pattern)
            for choice in choices:
                indexed[choice].append(choices)
        return indexed

    def _discover(
        self, occurrence: SemanticOccurrence[RenderTargetT, ContextT], *, speculative: bool = False
    ) -> (
        tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
        | OfferDiscovery[RenderTargetT, ContextT, PayloadT]
    ):
        discovery = self.discoveries.get(id(occurrence))
        if discovery is None:
            discovery = self.rules.offers(
                occurrence,
                replace(self.context, palette=occurrence.palette) if occurrence.palette is not None else self.context,
            )
            if (
                not speculative
                or not isinstance(discovery, OfferDiscovery)
                or discovery.completion != DiscoveryCompletion.EXHAUSTED
            ):
                self.discoveries[id(occurrence)] = discovery
                self.discovery_owners[id(occurrence)] = occurrence
        return discovery

    def check_root_bound(self, rejection: tuple[CandidateViolation, ...]) -> tuple[CapacityExceeded, ...]:
        """Try a proof after a capacity rejection, reserving work for ordinary search."""
        if self.bounds_checked or not any(isinstance(item, CapacityExceeded) for item in rejection):
            return ()
        self.bounds_checked = True
        try:
            with self.context.work.allowance(self.context.work.remaining // 8):
                minimum = self._minimum(self.root)
        except LayoutSearchExhaustedError:
            return ()
        return tuple(
            CapacityExceeded(self.root.identity, axis, cost, capacity)
            for axis, cost, capacity in minimum.over(self.context.capacities)
        )

    def _minimum(self, occurrence: SemanticOccurrence[RenderTargetT, ContextT]) -> ResourceCost:
        key = id(occurrence)
        if (cached := self.minimums.get(key)) is not None:
            return cached
        self.context.work.consume()
        discovery = self._discover(occurrence, speculative=True)
        if isinstance(discovery, OfferDiscovery):
            if discovery.completion != DiscoveryCompletion.COMPLETE:
                # An undiscovered offer could have a lower cost.
                return EMPTY_COST
            domain = discovery.offers
        else:
            domain = discovery
        minimum: ResourceCost | None = None
        for offer in domain:
            self.context.work.consume()
            cost = offer.minimum_cost
            if cost is None or offer.region is not None:
                minimum = EMPTY_COST
                break
            for child in offer.children:
                if (child_cost := self._minimum(child)).values:
                    cost += child_cost
            if offer.minimum_subtree_cost.values:
                cost = ResourceCost(
                    {
                        **cost.values,
                        **{
                            axis: max(cost.get(axis), amount)
                            for axis, amount in offer.minimum_subtree_cost.values.items()
                        },
                    }
                )
            minimum = (
                cost
                if minimum is None
                else ResourceCost({axis: min(amount, cost.get(axis)) for axis, amount in minimum.values.items()})
            )
            if not minimum.values:
                break
        result = minimum if minimum is not None else EMPTY_COST
        self.minimums[key] = result
        return result

    def branch(
        self, task: AssignmentTask[RenderTargetT, ContextT, PayloadT]
    ) -> Iterator[AssignmentTask[RenderTargetT, ContextT, PayloadT]]:
        """Yield eligible assignments in queue order for the next occurrence."""
        assert task.pending is not None
        occurrence = task.pending.siblings[task.pending.index]
        rest = task.pending.advance()
        self.seen_occurrences.add(occurrence.identity)
        self.context.work.consume()
        fresh = id(occurrence) not in self.explored
        self.explored.add(id(occurrence))
        discovery = self._discover(occurrence)
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
        eligible: list[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int]] = []
        for offer, rank in zip(offers, ranks, strict=True):
            self.context.work.consume()
            if offer.key in keys or offer.occurrence != occurrence:
                message = "offers must have unique keys and retain their requested occurrence"
                raise LayoutInvariantError(message)
            keys.add(offer.key)
            if occurrence.identity in self.selected and self.selected[occurrence.identity] != offer.key:
                continue
            if self.context.strict and offer.effects:
                continue
            rejections = offer.rejections or offer.atomic_violations(self.context)
            if rejections:
                self.pruned = True
                if fresh:
                    self.violations.extend(rejections)
                    if any(isinstance(violation, UnsupportedCapability) for violation in rejections):
                        self.unsupported.add(occurrence.identity)
                continue
            choice = (occurrence.identity, offer.key)
            if self.excluded and (patterns := self.exclusions_by_choice.get(choice)):
                for pattern in patterns:
                    self.context.work.consume(1 + len(pattern))
                    if all(item == choice or task.selected.get(item[0]) == item[1] for item in pattern):
                        self.pruned = True
                        break
                else:
                    eligible.append((offer, rank))
                continue
            eligible.append((offer, rank))
        self.choice_counts[occurrence.identity] = len(eligible)
        ordered = sorted(eligible, key=lambda entry: (entry[1], entry[0].key))
        if task.probe and ordered:
            if task.repair is not None:
                preferred = task.repair.get(occurrence.identity)
                ordered = next(([entry] for entry in ordered if entry[0].key == preferred), ordered[:1])
            else:
                position = min(len(ordered) - 1, int((len(ordered) - 1) * task.fraction + 0.5))
                ordered = ordered[position : position + 1]
        for offer, rank in ordered:
            if occurrence.identity in task.selected:
                message = "cyclic or duplicate occurrence in offer structure"
                raise LayoutInvariantError(message)
            ranks_key = task.ranks
            if rank and not task.probe:
                # Descending (rank, count) pairs compare like the expanded rank sequence.
                self.context.work.consume(len(ranks_key) + 1)
                counts = dict(ranks_key)
                counts[rank] = counts.get(rank, 0) + 1
                ranks_key = tuple(sorted(counts.items(), reverse=True))
            bound = task.pending.bound and offer.minimum_cost is not None and offer.region is None
            minimum = task.minimum_cost
            if bound and offer.minimum_cost is not None and offer.minimum_cost.values:
                minimum += offer.minimum_cost
            pending = Pending(offer.children, tail=rest, bound=bound) if offer.children else rest
            # Repair probes use a rejected candidate to choose their next
            # structural step. Let them reach certification.
            if task.repair is None and self.minimums and minimum.values:
                optimistic = minimum
                cursor = pending
                while cursor is not None:
                    if cursor.bound:
                        key = (id(cursor.siblings), cursor.index)
                        extra = self.suffix_minimums.get(key)
                        if extra is None:
                            suffix = EMPTY_COST
                            for sibling in cursor.siblings[cursor.index :]:
                                self.context.work.consume()
                                if (cost := self.minimums.get(id(sibling))) is not None and cost.values:
                                    suffix += cost
                            self.suffix_minimums[key] = suffix
                            extra = suffix
                        if extra.values:
                            optimistic += extra
                    cursor = cursor.tail
                if overflow := tuple(optimistic.over(self.context.capacities)):
                    self.pruned = True
                    for axis, cost, capacity in overflow:
                        rejection = CapacityExceeded(self.root.identity, axis, cost, capacity)
                        if rejection not in self.bound_rejections:
                            self.bound_rejections.add(rejection)
                            self.violations.append(rejection)
                    continue
            yield AssignmentTask(
                pending,
                task.history.push_front((offer, rank)),
                task.selected.insert(occurrence.identity, offer.key),
                ranks_key,
                probe=task.probe,
                fraction=task.fraction,
                repair=task.repair,
                minimum_cost=minimum,
            )
        if occurrence.identity in self.selected and self.selected[occurrence.identity] not in keys:
            message = f"unknown fixed offer for {occurrence.identity.path!r}"
            raise LayoutInvariantError(message)

    def repair(
        self, choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...]
    ) -> Iterator[AssignmentTask[RenderTargetT, ContextT, PayloadT]]:
        # Try one structural step at a time before the broad compact
        # probes. This finds useful fits under component pressure without
        # shortening every independent text ladder. Failed probes never prune.
        self.repair_started = True
        steps: list[tuple[int, int, tuple[str, ...], RealizationOffer[RenderTargetT, ContextT, PayloadT]]] = []
        for current, rank in choices:
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
                and not (offer.rejections or offer.atomic_violations(self.context))
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
            self.context.work.consume(len(choices))
            hints = {offer.occurrence.identity: offer.key for offer, _ in choices}
            hints[changed.occurrence.identity] = changed.key
            yield AssignmentTask(Pending((self.root,)), probe=True, repair=hints)

    def degradation_probes(
        self,
        choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...],
        *,
        has_regions: bool,
    ) -> Iterator[AssignmentTask[RenderTargetT, ContextT, PayloadT]]:
        # Find a compact certificate before enumerating combinations of
        # independent ladders. This changes work order only: ordinary
        # assignments remain queued and still establish optimality.
        self.degradation_probed = True
        # Intermediate rungs avoid an empty incumbent when several prose
        # blocks must share space. This samples work order, never feasibility.
        self.context.work.consume(len(choices))
        adjustable = 0
        for current, _ in choices:
            discovery = self.discoveries[id(current.occurrence)]
            domain = discovery.offers if isinstance(discovery, OfferDiscovery) else discovery
            self.context.work.consume(len(domain))
            if self.choice_counts.get(current.occurrence.identity, 0) > 1 and (
                not has_regions or any(offer.effects for offer in domain)
            ):
                adjustable += 1
        if adjustable:
            for fraction in dict.fromkeys((1.0, 1 - 1 / max(2, adjustable), 0.5, 0.75, 0.875, 0.9375)):
                yield AssignmentTask(Pending((self.root,)), probe=True, fraction=fraction)

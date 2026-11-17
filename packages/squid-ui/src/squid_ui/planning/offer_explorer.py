"""Explore representation offers and prioritize repairs after failed partitions.

One instance belongs to one joint search. Discovery results stay cached while
queued assignments refer to their occurrences, including partial discoveries
that mark the search exhausted without preventing a feasible certificate.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.allocation_policy import dense_ranks
from squid_ui.planning.composition import (
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


@dataclass(frozen=True, slots=True)
class AssignmentTask[RenderTargetT, ContextT, PayloadT]:
    pending: tuple[SemanticOccurrence[RenderTargetT, ContextT], ...]
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...] = ()
    probe: bool = False
    fraction: float = 1.0
    repair: Mapping[OccurrenceId, str] | None = None


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
    ) -> None:
        self.root = root
        self.rules = rules
        self.context = context
        self.selected = selections
        self.violations = violations
        self.unsupported = unsupported
        self.seen_occurrences: set[OccurrenceId] = set()
        self.degradation_probed = False
        self.repair_started = False
        self.exhausted = False
        self.choice_counts: dict[OccurrenceId, int] = {}
        # Keep each occurrence alive while its identity keys a cached discovery.
        self.discoveries: dict[
            int,
            tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], ...]
            | OfferDiscovery[RenderTargetT, ContextT, PayloadT],
        ] = {}
        self.discovery_owners: dict[int, SemanticOccurrence[RenderTargetT, ContextT]] = {}

    def branch(
        self, task: AssignmentTask[RenderTargetT, ContextT, PayloadT]
    ) -> Iterator[AssignmentTask[RenderTargetT, ContextT, PayloadT]]:
        """Yield eligible assignments in queue order for the next occurrence."""
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
            yield AssignmentTask(
                (*offer.children, *rest),
                (*task.choices, (offer, rank)),
                probe=task.probe,
                fraction=task.fraction,
                repair=task.repair,
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
            yield AssignmentTask((self.root,), probe=True, repair=hints)

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
                yield AssignmentTask((self.root,), probe=True, fraction=fraction)

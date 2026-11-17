"""Measure allocation quality from certified page costs and authored preferences."""

from collections.abc import Collection, Mapping

from squid_ui.authoring.presentations import FidelityEffect
from squid_ui.planning.allocation import PageFamily
from squid_ui.planning.allocation_policy import AllocationQuality, PreferenceTree, RegionPolicy
from squid_ui.planning.certification import FamilyCertificate
from squid_ui.planning.composition import CandidateComposition, OccurrenceId, PlanningContext, RealizationOffer
from squid_ui.planning.resources import EMPTY_COST, Axis


def preference_tree[RenderTargetT, ContextT, PayloadT](
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
                children.append((priority, preference_tree(child, ranks, policies, context)))
            else:
                visit(child)

    visit(candidate)
    return PreferenceTree(tuple(sorted(own, reverse=True)), tuple(children))


def score_family[RenderTargetT, ContextT, PayloadT, OutputT](
    family: PageFamily[RenderTargetT, ContextT, PayloadT],
    choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...],
    regions: Collection[OccurrenceId],
    certificate: FamilyCertificate[OutputT],
    policies: Mapping[OccurrenceId, RegionPolicy],
    context: PlanningContext,
) -> tuple[AllocationQuality, bool]:
    """Return the family's quality and whether every soft goal and break is met."""
    ranks = {offer.occurrence.identity: rank for offer, rank in choices}
    preferences = preference_tree(family.root, ranks, policies, context)
    minimums: list[int] = []
    for axis in Axis:
        context.work.consume(1 + sum(len(value.goals) for value in policies.values()))
        deficits = [
            max(0, goal.minimum - certificate.peaks.get(identity, EMPTY_COST).get(axis)) if identity in regions else 0
            for identity, region_policy in policies.items()
            for goal in region_policy.goals
            if goal.axis == axis and not goal.hard
        ]
        minimums.extend(sorted(deficits, reverse=True))
    effects: frozenset[FidelityEffect] = frozenset(effect for offer, _ in choices for effect in offer.effects)
    tie = tuple(
        sorted(
            (
                offer.occurrence.identity.path,
                offer.key,
                family.regions[offer.occurrence.identity].cuts if offer.occurrence.identity in family.regions else (),
            )
            for offer, _ in choices
        )
    )
    break_shortfalls = tuple(sorted(certificate.break_shortfalls.values(), reverse=True))
    quality = AllocationQuality(
        tuple(minimums),
        preferences,
        effects,
        break_shortfalls,
        sum(len(region.cuts) - 1 for region in family.regions.values()),
        tie,
    )
    satisfied = not any(minimums) and not any(break_shortfalls)
    return quality, satisfied

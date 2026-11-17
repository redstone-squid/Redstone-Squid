"""Retain certified page families that are not dominated in allocation quality."""

from collections.abc import Collection, Mapping
from dataclasses import replace
from types import MappingProxyType

from squid_ui.planning.allocation import AllocationSnapshot, PageFamily
from squid_ui.planning.allocation_policy import AllocationQuality, RegionPolicy, compare_quality
from squid_ui.planning.allocation_scoring import score_family
from squid_ui.planning.certification import FamilyCertificate
from squid_ui.planning.composition import OccurrenceId, PlanningContext, RealizationOffer
from squid_ui.planning.resources import EMPTY_COST


class AllocationFrontier[RenderTargetT, ContextT, PayloadT, OutputT]:
    """Keep complete certified snapshots for the nondominated search results.

    One frontier belongs to one search. A certificate enters it only after all
    reachable outputs have been built within the shared work allowance.
    """

    def __init__(self, context: PlanningContext, policies: Mapping[OccurrenceId, RegionPolicy]) -> None:
        self.context = context
        self.policies = policies
        self._retained: list[tuple[AllocationQuality, AllocationSnapshot[OutputT]]] = []

    @property
    def has_result(self) -> bool:
        return bool(self._retained)

    def qualities(self) -> tuple[AllocationQuality, ...]:
        """Return incumbent qualities for optimistic assignment pruning."""
        return tuple(quality for quality, _ in self._retained)

    def consider(
        self,
        family: PageFamily[RenderTargetT, ContextT, PayloadT],
        choices: tuple[tuple[RealizationOffer[RenderTargetT, ContextT, PayloadT], int], ...],
        regions: Collection[OccurrenceId],
        certificate: FamilyCertificate[OutputT],
    ) -> bool:
        """Score and retain a feasible family; return whether it meets all soft goals and breaks."""
        self.context.work.consume(1 + len(choices) + len(regions) + len(self.policies) + len(self._retained))
        quality, satisfied = score_family(family, choices, regions, certificate, self.policies, self.context)
        if not any(
            compare_quality(previous, quality) == -1
            or (compare_quality(previous, quality) == 0 and previous.tie <= quality.tie)
            for previous, _ in self._retained
        ):
            self.context.work.consume(len(self._retained) + len(certificate.pages) + len(regions) + len(choices))
            survivors = [
                (previous, allocation)
                for previous, allocation in self._retained
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
                        ((offer.occurrence.identity, offer.key) for offer, _ in choices),
                        key=lambda item: item[0].path,
                    )
                ),
            )
            self._retained = [*survivors, (quality, allocation)]
        return satisfied

    def best(self) -> tuple[AllocationSnapshot[OutputT], tuple[AllocationQuality, ...]]:
        """Return the selected snapshot and all nondominated qualities in tie order.

        Call after `has_result` confirms that the search retained a family.
        """
        _, allocation = min(self._retained, key=lambda entry: entry[0].tie)
        qualities = tuple(entry[0] for entry in sorted(self._retained, key=lambda entry: entry[0].tie))
        return allocation, qualities

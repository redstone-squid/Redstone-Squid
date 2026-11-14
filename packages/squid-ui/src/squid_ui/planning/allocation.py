"""Immutable page families, independent of mounted cursor and delivery state.

An allocation stores one presentation and partition per logical region. Its
visible page combinations include nested regions only where their parent page
contains them. Certification stores the built output for each combination, so a
cursor change can select output without rerunning search or changing a sibling.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Protocol
from uuid import uuid4

from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import CandidateComposition, NavigationInput, OccurrenceId, PlanningContext
from squid_ui.planning.fragments import FragmentTree, slice_fragment
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.presentations import SemanticRef


@dataclass(frozen=True, slots=True)
class RegionAllocation[RenderTargetT, ContextT, PayloadT]:
    identity: OccurrenceId
    key: str
    cuts: tuple[int, ...]
    pages: tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...]
    anchors: tuple[frozenset[SemanticRef], ...]
    allocation: ResourceCost = EMPTY_COST


@dataclass(frozen=True, slots=True)
class PageFamily[RenderTargetT, ContextT, PayloadT]:
    root: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    regions: Mapping[OccurrenceId, RegionAllocation[RenderTargetT, ContextT, PayloadT]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", MappingProxyType(dict(self.regions)))

    def combinations(
        self, context: PlanningContext
    ) -> Iterator[tuple[tuple[tuple[OccurrenceId, int], ...], CandidateComposition[RenderTargetT, ContextT, PayloadT]]]:
        def visit(
            part: CandidateComposition[RenderTargetT, ContextT, PayloadT],
        ) -> Iterator[
            tuple[tuple[tuple[OccurrenceId, int], ...], CandidateComposition[RenderTargetT, ContextT, PayloadT]]
        ]:
            context.work.consume()
            if part.offer.region is not None:
                occurrence = part.offer.occurrence
                identity = occurrence.fragment.logical if occurrence.fragment is not None else occurrence.identity
                region = self.regions[identity]
                for index, page in enumerate(region.pages):
                    context.work.consume()
                    for coordinates, visible in visit(page):
                        yield ((identity, index), *coordinates), visible
                return

            def children(
                index: int,
                coordinates: tuple[tuple[OccurrenceId, int], ...],
                selected: tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...],
            ) -> Iterator[
                tuple[tuple[tuple[OccurrenceId, int], ...], CandidateComposition[RenderTargetT, ContextT, PayloadT]]
            ]:
                context.work.consume()
                if index == len(part.children):
                    yield (
                        coordinates,
                        CandidateComposition(
                            replace(part.offer, children=tuple(child.offer.occurrence for child in selected)), selected
                        ),
                    )
                else:
                    for positions, child in visit(part.children[index]):
                        yield from children(index + 1, (*coordinates, *positions), (*selected, child))

            yield from children(0, (), ())

        yield from visit(self.root)


def allocate_family[RenderTargetT, ContextT, PayloadT](
    root: CandidateComposition[RenderTargetT, ContextT, PayloadT],
    regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...],
    cuts: tuple[tuple[int, ...], ...],
    context: PlanningContext,
) -> PageFamily[RenderTargetT, ContextT, PayloadT]:
    allocated: dict[OccurrenceId, RegionAllocation[RenderTargetT, ContextT, PayloadT]] = {}
    for node, ends in zip(regions, cuts, strict=True):
        context.work.consume()
        offer = node.candidate.offer
        assert offer.region is not None
        if ends != tuple(sorted(set(ends))) or not ends or ends[-1] != len(node.units):
            message = "region cuts must cover every unit exactly once"
            raise LayoutInvariantError(message)
        pages: list[CandidateComposition[RenderTargetT, ContextT, PayloadT]] = []
        anchors: list[frozenset[SemanticRef]] = []
        start = 0
        for index, end in enumerate(ends):
            context.work.consume()
            fragment = slice_fragment(node, start, end, context)
            chrome = offer.region.navigation(index, len(ends), context)
            children = (*fragment.children, *chrome)
            pages.append(
                CandidateComposition(
                    replace(fragment.offer, children=tuple(child.offer.occurrence for child in children)), children
                )
            )
            anchors.append(node.units[start] if node.units else frozenset())
            start = end
        allocated[offer.occurrence.identity] = RegionAllocation(
            offer.occurrence.identity, offer.region.key, ends, tuple(pages), tuple(anchors)
        )
    return PageFamily(root, allocated)


@dataclass(frozen=True, slots=True)
class CertifiedPage[OutputT]:
    coordinates: tuple[tuple[OccurrenceId, int], ...]
    output: OutputT


@dataclass(frozen=True, slots=True)
class RegionPages:
    key: str
    cuts: tuple[int, ...]
    anchors: tuple[frozenset[SemanticRef], ...]
    allocation: ResourceCost
    navigation: tuple[tuple[NavigationInput, ...], ...]


class AllocationSelection[OutputT](Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def regions(self) -> Mapping[OccurrenceId, RegionPages]: ...

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> OutputT: ...


@dataclass(frozen=True, slots=True)
class CertifiedAllocation[RenderTargetT, ContextT, PayloadT, OutputT]:
    """A request-local allocation identity and all certified visible page combinations.

    Reuse this object for cursor movement. Content, target, localization, policy,
    or resource changes require a new search. This is not a cross-request cache
    key: phase 4 owns mounted revisions and phase 5 owns reusable fingerprints.
    """

    family: PageFamily[RenderTargetT, ContextT, PayloadT]
    pages: tuple[CertifiedPage[OutputT], ...]
    selection: tuple[tuple[OccurrenceId, str], ...]
    identity: str = field(init=False)
    regions: Mapping[OccurrenceId, RegionPages] = field(init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "identity", uuid4().hex)
        object.__setattr__(
            self,
            "regions",
            MappingProxyType(
                {
                    identity: RegionPages(
                        region.key,
                        region.cuts,
                        region.anchors,
                        region.allocation,
                        tuple(
                            tuple(
                                child.offer.navigation for child in page.children if child.offer.navigation is not None
                            )
                            for page in region.pages
                        ),
                    )
                    for identity, region in self.family.regions.items()
                }
            ),
        )

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> OutputT:
        """Select clamped zero-based coordinates; hidden nested coordinates are ignored."""
        positions = coordinates or {}
        if positions.keys() - self.family.regions.keys():
            message = "page coordinates contain an unknown region occurrence"
            raise LayoutInvariantError(message)
        for page in self.pages:
            if all(
                index == max(0, min(positions.get(identity, 0), len(self.family.regions[identity].pages) - 1))
                for identity, index in page.coordinates
            ):
                return page.output
        message = "certified allocation lost a reachable page combination"
        raise LayoutInvariantError(message)

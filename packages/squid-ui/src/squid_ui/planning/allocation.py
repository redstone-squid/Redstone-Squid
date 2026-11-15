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

from squid_ui.authoring.presentations import SemanticRef
from squid_ui.errors import LayoutInvariantError
from squid_ui.planning.composition import (
    CandidateComposition,
    NavigationInput,
    OccurrenceId,
    Placement,
    PlanningContext,
    RegionContract,
)
from squid_ui.planning.fragments import FragmentTree, slice_fragment
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.primitives.constraints import FIRST_PAGE, PageOwnership


@dataclass(frozen=True, slots=True)
class RegionAllocation[RenderTargetT, ContextT, PayloadT]:
    identity: OccurrenceId
    key: str
    cuts: tuple[int, ...]
    pages: tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...]
    anchors: tuple[frozenset[SemanticRef], ...]
    allocation: ResourceCost = EMPTY_COST
    source_order: tuple[SemanticRef, ...] = ()


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
        nested: list[tuple[frozenset[SemanticRef], SemanticRef]] = []
        pending = list(node.children)
        while pending:
            child = pending.pop()
            child_offer = child.candidate.offer
            if child_offer.region is not None:
                nested.append((child_offer.occurrence.sources, child_offer.occurrence.page_source))
            else:
                pending.extend(child.children)
        anchor_units: list[frozenset[SemanticRef]] = []
        for unit in node.units:
            sources = set(unit)
            for covered, reference in nested:
                if covered and covered <= sources:
                    sources.difference_update(covered)
                    sources.add(reference)
            anchor_units.append(frozenset(sources))
        pages: list[CandidateComposition[RenderTargetT, ContextT, PayloadT]] = []
        anchors: list[frozenset[SemanticRef]] = []
        start = 0
        for index, end in enumerate(ends):
            context.work.consume()
            fragment = slice_fragment(node, start, end, context)
            chrome = offer.region.navigation(index, len(ends), context)
            children = (
                *(child for child in fragment.children if child.offer.occurrence.placement != Placement.FOOTER),
                *chrome,
                *(child for child in fragment.children if child.offer.occurrence.placement == Placement.FOOTER),
            )
            pages.append(
                CandidateComposition(
                    replace(fragment.offer, children=tuple(child.offer.occurrence for child in children)), children
                )
            )
            anchors.append(frozenset(source for unit in anchor_units[start:end] for source in unit))
            start = end
        sources = frozenset(source for unit in anchor_units for source in unit)
        ordered = tuple(ref for ref in root.offer.occurrence.graph.units if ref in sources)
        allocated[offer.occurrence.identity] = RegionAllocation(
            offer.occurrence.identity, offer.region.key, ends, tuple(pages), tuple(anchors), source_order=ordered
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
    source_order: tuple[SemanticRef, ...] = ()
    owner: PageOwnership = FIRST_PAGE
    cursor_key: str = ""
    source_prefix: tuple[str, ...] = ()


class AllocationSelection[OutputT](Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def regions(self) -> Mapping[OccurrenceId, RegionPages]: ...

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> OutputT: ...

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]: ...


@dataclass(frozen=True, slots=True)
class CertifiedAllocation[RenderTargetT, ContextT, PayloadT, OutputT]:
    """A request-local allocation identity and all certified visible page combinations.

    Reuse this object for cursor movement. Content, target, localization, policy,
    or resource changes require a new search. This is not a cross-request cache
    key; shared caches store only partition hints and recertify current output.
    """

    family: PageFamily[RenderTargetT, ContextT, PayloadT]
    pages: tuple[CertifiedPage[OutputT], ...]
    selection: tuple[tuple[OccurrenceId, str], ...]
    identity: str = field(init=False)
    regions: Mapping[OccurrenceId, RegionPages] = field(init=False)

    _page_index: Mapping[tuple[OccurrenceId, ...], Mapping[tuple[int, ...], CertifiedPage[OutputT]]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        index: dict[tuple[OccurrenceId, ...], dict[tuple[int, ...], CertifiedPage[OutputT]]] = {}
        for page in self.pages:
            identities = tuple(identity for identity, _ in page.coordinates)
            positions = tuple(position for _, position in page.coordinates)
            index.setdefault(identities, {})[positions] = page
        object.__setattr__(self, "_page_index", MappingProxyType({k: MappingProxyType(v) for k, v in index.items()}))
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
                        region.source_order,
                        self._contract(identity).owner,
                        self._contract(identity).cursor_key or region.key,
                        region.pages[0].offer.occurrence.page_source.keys,
                    )
                    for identity, region in self.family.regions.items()
                }
            ),
        )

    def _contract(self, identity: OccurrenceId) -> RegionContract[RenderTargetT, ContextT, PayloadT]:
        pending = [self.family.root]
        while pending:
            part = pending.pop()
            if part.offer.occurrence.identity == identity and part.offer.region is not None:
                return part.offer.region
            pending.extend(part.children)
        msg = "allocation lost its region contract"
        raise LayoutInvariantError(msg)

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]:
        return dict(self._page(coordinates).coordinates)

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> OutputT:
        """Select clamped zero-based coordinates; hidden nested coordinates are ignored."""
        return self._page(coordinates).output

    def _page(self, coordinates: Mapping[OccurrenceId, int] | None) -> CertifiedPage[OutputT]:
        positions = coordinates or {}
        if positions.keys() - self.family.regions.keys():
            message = "page coordinates contain an unknown region occurrence"
            raise LayoutInvariantError(message)
        # Nested pages have different visible-region sets. Index each set separately
        # so sibling Cartesian products do not make every cursor turn a linear scan.
        for identities, pages in self._page_index.items():
            key = tuple(
                max(0, min(positions.get(identity, 0), len(self.family.regions[identity].pages) - 1))
                for identity in identities
            )
            if page := pages.get(key):
                return page
        message = "certified allocation lost a reachable page combination"
        raise LayoutInvariantError(message)

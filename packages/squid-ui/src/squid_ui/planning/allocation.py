"""Immutable page families, independent of mounted cursor and delivery state.

An allocation stores one presentation and partition per logical region. Its
visible page combinations include nested regions only where their parent page
contains them. Certification stores the built output for each combination, so a
cursor change can select output without rerunning search or changing a sibling.
"""

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from squid_ui.authoring.presentations import SemanticRef
from squid_ui.errors import LayoutInvariantError
from squid_ui.overflow import FIRST_PAGE, PageOwnership
from squid_ui.planning.composition import (
    CandidateComposition,
    OccurrenceId,
    Placement,
    PlanningContext,
    RegionContract,
)
from squid_ui.planning.context_evaluation import evaluate_contextual
from squid_ui.planning.fragments import FragmentTree, slice_fragment
from squid_ui.planning.resources import EMPTY_COST, ResourceCost

if TYPE_CHECKING:
    from squid_ui.layout import LayoutContext


@dataclass(frozen=True, slots=True)
class RegionAllocation[RenderTargetT, ContextT, PayloadT]:
    identity: OccurrenceId
    key: str
    cuts: tuple[int, ...]
    pages: tuple[CandidateComposition[RenderTargetT, ContextT, PayloadT], ...]
    anchors: tuple[frozenset[SemanticRef], ...]
    contract: RegionContract[RenderTargetT, ContextT, PayloadT]
    allocation: ResourceCost = EMPTY_COST
    source_order: tuple[SemanticRef, ...] = ()

    @property
    def source_prefix(self) -> tuple[str, ...]:
        return self.pages[0].offer.occurrence.page_source.keys

    @property
    def cursor_key(self) -> str:
        return self.contract.cursor_key or self.contract.key

    @property
    def owner(self) -> PageOwnership:
        return self.contract.owner


@dataclass(frozen=True, slots=True)
class PageFamily[RenderTargetT, ContextT, PayloadT]:
    root: CandidateComposition[RenderTargetT, ContextT, PayloadT]
    regions: Mapping[OccurrenceId, RegionAllocation[RenderTargetT, ContextT, PayloadT]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", MappingProxyType(dict(self.regions)))

    @classmethod
    def from_cuts(
        cls,
        root: CandidateComposition[RenderTargetT, ContextT, PayloadT],
        regions: tuple[FragmentTree[RenderTargetT, ContextT, PayloadT], ...],
        cuts: tuple[tuple[int, ...], ...],
        context: PlanningContext,
    ) -> PageFamily[RenderTargetT, ContextT, PayloadT]:
        """Build source fragments from legal cuts; contextual chrome is resolved per view."""
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
            for end in ends:
                context.work.consume()
                fragment = slice_fragment(node, start, end, context)
                pages.append(fragment)
                anchors.append(frozenset(source for unit in anchor_units[start:end] for source in unit))
                start = end
            sources = frozenset(source for unit in anchor_units for source in unit)
            ordered = tuple(ref for ref in root.offer.occurrence.graph.units if ref in sources)
            allocated[offer.occurrence.identity] = RegionAllocation(
                offer.occurrence.identity,
                offer.region.key,
                ends,
                tuple(pages),
                tuple(anchors),
                offer.region,
                source_order=ordered,
            )
        return cls(root, allocated)

    def contextualize(
        self,
        part: CandidateComposition[RenderTargetT, ContextT, PayloadT],
        layout: LayoutContext,
        context: PlanningContext,
    ) -> CandidateComposition[RenderTargetT, ContextT, PayloadT]:
        context.work.consume()
        children = tuple(self.contextualize(child, layout, context) for child in part.children)
        coverage = part.offer.occurrence.fragment
        if coverage is not None and coverage.logical in self.regions:
            chrome = self.regions[coverage.logical].contract.chrome
            scoped = layout._evaluating(context.work.consume, containing=coverage.logical)
            output = evaluate_contextual(chrome.content, scoped)
            generated = chrome.realize(output, context)
            children = (
                *(child for child in children if child.offer.occurrence.placement != Placement.FOOTER),
                *generated,
                *(child for child in children if child.offer.occurrence.placement == Placement.FOOTER),
            )
        return CandidateComposition(
            replace(part.offer, children=tuple(child.offer.occurrence for child in children)), children
        )

    def combinations(
        self, context: PlanningContext, selected: Mapping[OccurrenceId, int] | None = None
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
                index = min(max(0, selected.get(identity, 0)), len(region.pages) - 1) if selected is not None else 0
                pages = enumerate(region.pages) if selected is None else ((index, region.pages[index]),)
                for index, page in pages:
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


def _index_pages[OutputT](
    pages: tuple[CertifiedPage[OutputT], ...],
) -> Mapping[tuple[OccurrenceId, ...], Mapping[tuple[int, ...], CertifiedPage[OutputT]]]:
    index: dict[tuple[OccurrenceId, ...], dict[tuple[int, ...], CertifiedPage[OutputT]]] = {}
    for page in pages:
        identities = tuple(identity for identity, _ in page.coordinates)
        positions = tuple(position for _, position in page.coordinates)
        index.setdefault(identities, {})[positions] = page
    return MappingProxyType({key: MappingProxyType(value) for key, value in index.items()})


def _select_page[OutputT](
    index: Mapping[tuple[OccurrenceId, ...], Mapping[tuple[int, ...], CertifiedPage[OutputT]]],
    regions: Mapping[OccurrenceId, RegionPages],
    coordinates: Mapping[OccurrenceId, int] | None,
) -> CertifiedPage[OutputT]:
    positions = coordinates or {}
    if positions.keys() - regions.keys():
        message = "page coordinates contain an unknown region occurrence"
        raise LayoutInvariantError(message)
    # Nested pages have different visible-region sets. Index each set separately
    # so sibling Cartesian products do not make every cursor turn a linear scan.
    for identities, pages in index.items():
        key = tuple(max(0, min(positions.get(identity, 0), len(regions[identity].cuts) - 1)) for identity in identities)
        if page := pages.get(key):
            return page
    message = "certified allocation lost a reachable page combination"
    raise LayoutInvariantError(message)


@dataclass(frozen=True, slots=True)
class AllocationSnapshot[OutputT]:
    """Certified outputs and navigation metadata without the search's authored graph.

    Output conversion preserves coordinates and allocation identity. It must not
    change layout or resource costs; this operation does not recertify the pages.
    Outputs and region owners may still contain live bindings, so snapshots remain
    owner-local unless their producer explicitly removes those references.
    """

    identity: str
    regions: Mapping[OccurrenceId, RegionPages]
    pages: tuple[CertifiedPage[OutputT], ...]
    selection: tuple[tuple[OccurrenceId, str], ...] = ()
    _page_index: Mapping[tuple[OccurrenceId, ...], Mapping[tuple[int, ...], CertifiedPage[OutputT]]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "regions", MappingProxyType(dict(self.regions)))
        object.__setattr__(self, "_page_index", _index_pages(self.pages))

    @classmethod
    def certify[RenderTargetT, ContextT, PayloadT](
        cls,
        family: PageFamily[RenderTargetT, ContextT, PayloadT],
        pages: tuple[CertifiedPage[OutputT], ...],
        selection: tuple[tuple[OccurrenceId, str], ...],
    ) -> AllocationSnapshot[OutputT]:
        """Capture a certified family's metadata without retaining its search graph."""
        contracts = {}
        pending = [family.root]
        while pending:
            part = pending.pop()
            if part.offer.region is not None:
                contracts[part.offer.occurrence.identity] = part.offer.region
            pending.extend(part.children)
        regions = {
            identity: RegionPages(
                region.key,
                region.cuts,
                region.anchors,
                region.allocation,
                region.source_order,
                contracts[identity].owner,
                contracts[identity].cursor_key or region.key,
                region.pages[0].offer.occurrence.page_source.keys,
            )
            for identity, region in family.regions.items()
        }
        return cls(uuid4().hex, regions, pages, selection)

    @classmethod
    def project[SourceT](
        cls,
        source: AllocationSnapshot[SourceT],
        convert: Callable[[SourceT], OutputT],
        *,
        regions: Mapping[OccurrenceId, RegionPages] | None = None,
    ) -> AllocationSnapshot[OutputT]:
        return cls(
            source.identity,
            source.regions if regions is None else regions,
            tuple(CertifiedPage(page.coordinates, convert(page.output)) for page in source.pages),
            source.selection,
        )

    def visible(self, coordinates: Mapping[OccurrenceId, int]) -> Mapping[OccurrenceId, int]:
        return dict(_select_page(self._page_index, self.regions, coordinates).coordinates)

    def select(self, coordinates: Mapping[OccurrenceId, int] | None = None) -> OutputT:
        return _select_page(self._page_index, self.regions, coordinates).output

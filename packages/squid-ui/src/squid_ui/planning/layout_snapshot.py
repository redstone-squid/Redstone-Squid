"""Capture canonical source occurrences and page-fragment provenance during planning."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any

from squid_ui.authoring.presentations import Alternatives, Omission, SemanticRef
from squid_ui.authoring.semantic import FallbackContent, List, OptionalContent
from squid_ui.layout import (
    ContentAddress,
    DocumentScope,
    ElementRef,
    FragmentLocation,
    Known,
    LayoutContext,
    Locations,
    PageAddress,
    PageCoordinate,
    RegionFacts,
    RegionRef,
    Unknown,
    UnknownReason,
)
from squid_ui.planning.allocation import PageFamily
from squid_ui.planning.composition import CandidateComposition, OccurrenceId, Placement, PlanningContext
from squid_ui.planning.semantic_index import SemanticGraph
from squid_ui.selectors import Match, Query, Selector, TreeIndex, query, select


@dataclass(frozen=True, slots=True)
class _Fragment:
    source: SemanticRef
    page: tuple[tuple[OccurrenceId, int], ...]
    span: tuple[int, int] | None
    repeated: bool


class SemanticCapture:
    """A canonical graph index, independent of the target representation's shape."""

    def __init__(self, graph: SemanticGraph[Any, Any], consume: Callable[[int], None]) -> None:
        self.references = tuple(graph.units)
        positions = {ref: i for i, ref in enumerate(self.references)}
        self.positions = MappingProxyType(positions)
        token = object()
        matches: list[Match[object]] = []
        parents: list[int | None] = []
        ends: list[int] = []
        for i, reference in enumerate(self.references):
            consume(1)
            unit = graph.units[reference]
            key = reference.keys[-1]
            key = None if key.startswith("@") else key
            matches.append(Match(unit.value, "/".join(reference.keys), None, key, key, i, token))
            parents.append(positions[unit.parent] if unit.parent is not None else None)
            ends.append(i + 1)
        for i in range(len(ends) - 1, -1, -1):
            if (parent := parents[i]) is not None:
                ends[parent] = max(ends[parent], ends[i])
        self.index = TreeIndex.capture(tuple(matches), tuple(parents), tuple(ends), token)


@dataclass(frozen=True, slots=True)
class _ScopeReader:
    capture: SemanticCapture
    members: frozenset[SemanticRef]
    consume: Callable[[int], None] | None = None

    def __call__[T](self, selector: Selector[T]) -> Query[T]:
        if self.consume is not None:
            self.consume(len(self.capture.references))
        membership = Selector[object](
            lambda _tree, index, _scope, _locale: self.capture.references[index] in self.members,
            "in captured layout scope",
        )
        return query(self.capture.index, selector.intersect(membership))


@dataclass(frozen=True, slots=True)
class _LocationReader:
    capture: SemanticCapture
    locations: Mapping[SemanticRef, tuple[FragmentLocation, ...]]
    regions: Mapping[RegionRef, RegionFacts]
    complete: bool
    consume: Callable[[int], None] | None

    def __call__[T](self, target: ElementRef[T] | Match[T], within: RegionRef | None) -> Locations:
        if self.consume is not None:
            self.consume(len(self.capture.references))
        if isinstance(target, Match):
            reference = self.capture.references[self.capture.index.index(target)]
        else:
            candidates = tuple(
                match
                for match in query(self.capture.index, select(target.kind)).all()
                if self.capture.references[match._index].keys[-len(target.keys) :] == target.keys
            )
            if len(candidates) > 1:
                message = f"ambiguous element reference: {target.keys!r}"
                raise ValueError(message)
            if not candidates:
                return Locations((), self.complete)
            reference = self.capture.references[candidates[0]._index]
        locations = self.locations.get(reference, ())
        if within is not None:
            regions = tuple(
                value for ref, value in self.regions.items() if ref.keys[-len(within.keys) :] == within.keys
            )
            if len(regions) != 1:
                message = f"region reference is absent or ambiguous: {within.keys!r}"
                raise ValueError(message)
            identity = regions[0].identity
            locations = tuple(
                loc for loc in locations if any(coord.region == identity for coord in loc.page.coordinates)
            )
        return Locations(locations, self.complete)


class LayoutSnapshot:
    """Immutable facts for a materialized family, reusable across its certified views."""

    def __init__[R, C, P](self, family: PageFamily[R, C, P], context: PlanningContext) -> None:
        graph = family.root.offer.occurrence.graph
        choices: list[tuple[OccurrenceId, str]] = []
        pending = [family.root]
        while pending:
            part = pending.pop()
            occurrence = part.offer.occurrence
            value = occurrence.graph.units[occurrence.reference].value
            if isinstance(value, Alternatives | FallbackContent | OptionalContent):
                choices.append((occurrence.identity, part.offer.key))
            pending.extend(part.children)
        self.choices = tuple(choices)
        self.capture = SemanticCapture(graph, context.work.consume)
        self.region_refs = {
            identity: RegionRef(region.pages[0].offer.occurrence.page_source.keys)
            for identity, region in family.regions.items()
        }
        self.counts = {identity: len(region.pages) for identity, region in family.regions.items()}
        self.groups: dict[OccurrenceId, int | None] = {}
        for identity, ref in self.region_refs.items():
            unit = graph.units.get(SemanticRef(ref.keys))
            self.groups[identity] = (
                unit.value.group_count if unit is not None and isinstance(unit.value, List) else None
            )
        self.captures: dict[tuple[tuple[OccurrenceId, int], ...], SemanticCapture] = {}
        self.visible: dict[tuple[tuple[OccurrenceId, int], ...], frozenset[SemanticRef]] = {}
        fragments: dict[SemanticRef, dict[_Fragment, None]] = {}

        def add(source: SemanticRef, fragment: _Fragment) -> None:
            if source in graph.units:
                fragments.setdefault(source, {})[replace(fragment, source=source)] = None

        def visit(
            part: CandidateComposition[R, C, P],
            coordinates: Mapping[OccurrenceId, int],
            chain: tuple[tuple[OccurrenceId, int], ...],
            repeated: frozenset[SemanticRef],
            span: tuple[int, int] | None,
            visible: set[SemanticRef],
        ) -> None:
            context.work.consume()
            occurrence = part.offer.occurrence
            if occurrence.placement == Placement.NAVIGATION:
                return
            if (coverage := occurrence.fragment) is not None:
                span = (coverage.start, coverage.end)
                repeated |= coverage.repeated
                if coverage.logical in coordinates and not any(identity == coverage.logical for identity, _ in chain):
                    chain = (*chain, (coverage.logical, coordinates[coverage.logical]))
            reference = occurrence.reference
            fragment = _Fragment(reference, chain, span, reference in repeated)
            omitted: set[SemanticRef] = set()
            for effect in part.offer.effects:
                if isinstance(effect, Omission):
                    omitted.update(graph.atoms(effect.source))
            if occurrence.origin is None and reference in graph.units and reference not in omitted:
                visible.add(reference)
                add(reference, fragment)
            if not part.children or part.offer.fragments is not None:
                for source in occurrence.sources - omitted:
                    context.work.consume()
                    visible.add(source)
                    add(source, replace(fragment, repeated=source in repeated))
            for child in part.children:
                visit(child, coordinates, chain, repeated, span, visible)

        for coordinates, visible in family.combinations(context):
            represented: set[SemanticRef] = set()
            visit(visible, dict(coordinates), (), frozenset(), None, represented)
            for source in tuple(represented):
                unit = graph.units.get(source)
                while unit is not None and unit.parent is not None:
                    context.work.consume()
                    represented.add(unit.parent)
                    unit = graph.units[unit.parent]
            self.visible[coordinates] = frozenset(represented)
            self.captures[coordinates] = SemanticCapture(graph, context.work.consume)
        # Correspondence can represent a canonical container without retaining
        # that container's target node. Derive only its missing ancestor facts.
        own_references = frozenset(fragments)
        for source, source_fragments in tuple(fragments.items()):
            parent = graph.units[source].parent
            while parent is not None:
                context.work.consume()
                if parent not in own_references:
                    for fragment in source_fragments:
                        add(parent, fragment)
                parent = graph.units[parent].parent
        self.placed = frozenset(fragments)
        self.fragments = MappingProxyType({ref: tuple(values) for ref, values in fragments.items()})

    def context(
        self,
        coordinates: Mapping[OccurrenceId, int],
        *,
        containing: OccurrenceId | None = None,
        consume: Callable[[int], None] | None = None,
        complete: bool = True,
    ) -> LayoutContext:
        selected = dict(coordinates)
        coordinate = next((key for key in self.visible if dict(key) == selected), None)
        if coordinate is None:
            message = "layout capture needs one reachable coordinate combination"
            raise ValueError(message)
        regions = {
            ref: RegionFacts(
                identity,
                Known(self.counts[identity])
                if complete
                else Unknown(UnknownReason.INCOMPLETE, "only this view was resolved"),
                Known(PageCoordinate(identity, Known(selected[identity]))) if identity in selected else Known(None),
                Known(groups)
                if (groups := self.groups[identity]) is not None
                else Unknown(UnknownReason.UNSUPPORTED, "no fixed item grouping was declared"),
            )
            for identity, ref in self.region_refs.items()
        }
        locations: dict[SemanticRef, tuple[FragmentLocation, ...]] = {}
        for source, fragments in self.fragments.items():
            locations[source] = tuple(
                FragmentLocation(
                    OccurrenceId(source.keys),
                    PageAddress(
                        tuple(
                            PageCoordinate(
                                identity,
                                Known(index),
                                ContentAddress(self.region_refs[identity], source.keys, fragment.span),
                            )
                            for identity, index in fragment.page
                        )
                    ),
                    ordinal,
                    fragment.repeated,
                    all(selected.get(identity) == index for identity, index in fragment.page),
                )
                for ordinal, fragment in enumerate(fragments)
                if complete or all(selected.get(identity) == index for identity, index in fragment.page)
            )
        capture = self.captures[coordinate]
        return LayoutContext(
            authored=DocumentScope(
                _ScopeReader(capture, frozenset(capture.references), consume), len(capture.references)
            ),
            placed=DocumentScope(_ScopeReader(capture, self.placed, consume), len(capture.references)),
            visible=DocumentScope(_ScopeReader(capture, self.visible[coordinate], consume), len(capture.references)),
            regions=MappingProxyType(regions),
            locate=_LocationReader(capture, MappingProxyType(locations), regions, complete, consume),
            containing=containing,
        )

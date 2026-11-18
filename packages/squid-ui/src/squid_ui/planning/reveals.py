"""Resolve accepted reveal commands against materialized candidate content."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from squid_ui.authoring.presentations import SemanticRef
from squid_ui.errors import LayoutError
from squid_ui.interactions.ownership import Controlled
from squid_ui.overflow import PageOwnership, RoutedPage, resolve_page
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.layout_snapshot import SemanticCapture
from squid_ui.planning.semantic_index import SemanticGraph
from squid_ui.presentation_state import RevealRequest


class _Region(Protocol):
    @property
    def anchors(self) -> tuple[frozenset[SemanticRef], ...]: ...

    @property
    def source_prefix(self) -> tuple[str, ...]: ...

    @property
    def cursor_key(self) -> str: ...

    @property
    def owner(self) -> PageOwnership: ...


@dataclass(frozen=True, slots=True)
class RevealResolution:
    request: RevealRequest
    coordinates: Mapping[OccurrenceId, int]

    def settled(self, desired: Mapping[OccurrenceId, int], visible: Mapping[OccurrenceId, int]) -> bool:
        superseded = any(desired.get(region) != page for region, page in self.coordinates.items())
        return superseded or all(visible.get(region) == page for region, page in self.coordinates.items())


class RevealResolver:
    def __init__(self, graph: SemanticGraph[Any, Any], consume: Callable[[int], None] = lambda _amount: None) -> None:
        self.capture = SemanticCapture(graph, consume)
        self.consume = consume

    def resolve(
        self,
        requests: Sequence[RevealRequest],
        regions: Mapping[OccurrenceId, _Region],
        position_sequence: Mapping[str, int],
    ) -> tuple[RevealResolution, ...]:
        resolutions: list[RevealResolution] = []
        for request in sorted(requests, key=lambda item: item.sequence):
            scope = request.scope
            coordinates: dict[OccurrenceId, int] = {}
            if scope.active and scope.cells.for_key(scope.path + ".") is scope:
                lineage = sorted(
                    {
                        candidate.path
                        for candidate in scope.cells.scopes.values()
                        if candidate.active
                        and (
                            candidate.path == scope.path
                            or candidate.path == "$"
                            or scope.path.startswith(candidate.path + ".")
                        )
                    },
                    key=lambda path: (path.count("."), len(path)),
                    reverse=True,
                )
                reference = request.reference
                matches = ()
                for path in lineage:
                    self.consume(1 + len(self.capture.references))
                    matches = tuple(
                        match
                        for match in self.capture.index.matches
                        if match.owner == path
                        and isinstance(match.node, reference.kind)
                        and (
                            self.capture.references[match._index].keys[-len(reference.keys) :] == reference.keys
                            or (len(reference.keys) == 1 and match.key == reference.keys[0])
                        )
                    )
                    if matches:
                        break
                if len(matches) > 1:
                    message = f"ambiguous reveal reference: {reference.keys!r}"
                    raise LayoutError(message)
                if matches:
                    target = self.capture.references[matches[0]._index].keys
                    for identity, region in regions.items():
                        self.consume(1)
                        if target[: len(region.source_prefix)] != region.source_prefix:
                            continue
                        for page, sources in enumerate(region.anchors):
                            self.consume(1 + len(sources))
                            if any(
                                source.keys[: len(target)] == target or target[: len(source.keys)] == source.keys
                                for source in sources
                            ):
                                if (
                                    isinstance(region.owner, Controlled | RoutedPage)
                                    and resolve_page(region.owner.value, len(region.anchors)) != page
                                ):
                                    message = "event.reveal() cannot move application-owned pagination"
                                    raise LayoutError(message)
                                coordinates[identity] = page
                                break
            if any(position_sequence.get(regions[region].cursor_key, 0) > request.sequence for region in coordinates):
                coordinates.clear()
            resolutions.append(RevealResolution(request, coordinates))
        return tuple(resolutions)

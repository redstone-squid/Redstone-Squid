"""Reconcile certified pages with their owners, staging the displayed state for delivery."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from urllib.parse import quote

from squid_ui import scene
from squid_ui.document import DocumentLike, as_document
from squid_ui.errors import LayoutInvariantError
from squid_ui.ownership import Controlled
from squid_ui.pages import PagePosition
from squid_ui.planning.composition import OccurrenceId
from squid_ui.planning.composition_request import CompositionOptions
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.identity import has_routed_page, stable_fingerprint
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanResult, RegionReport
from squid_ui.presentation import (
    ActivePagers,
    RegionAllocation,
    RegionLayoutState,
    RegionLayoutUpdate,
    RetainedCompositionUpdate,
)
from squid_ui.primitives.constraints import RoutedPage


def _environment(request: PlanRequest[Any, Any, Any]) -> tuple[object, ...]:
    key = request.exact_key()
    # Explicit positions select pages of the same certified family.
    return (*key[:6], *key[7:])


@dataclass(frozen=True, slots=True)
class RetainedComposition:
    document: object
    environment: tuple[object, ...]
    options: CompositionOptions
    result: PlanResult[Any]

    def matches(self, document: object, request: PlanRequest[Any, Any, Any], options: CompositionOptions) -> bool:
        # Route factories may encode changed external state behind the same callable.
        return (
            not has_routed_page(document)
            and document == self.document
            and _environment(request) == self.environment
            and replace(options, pages={}) == self.options
        )


def present_composition[BodyT: scene.Body, RenderTargetT, AdapterT](
    result: PlanResult[BodyT],
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    options: CompositionOptions,
) -> PlanResult[BodyT]:
    allocation = result.allocation
    assert allocation is not None
    document = as_document(rendered)
    fingerprint = stable_fingerprint((document,))
    overrides = {**request.presentation.pending_positions, **(request.positions or {})}
    coordinator = CursorCoordinator(request.presentation, request.chrome, overrides=overrides)
    coordinates: dict[OccurrenceId, int] = {}
    requests: dict[OccurrenceId, MaterializedCursorRequest] = {}
    positions: dict[OccurrenceId, PagePosition] = {}
    seen: set[str] = set()
    for identity, region in allocation.regions.items():
        key = region.cursor_key
        if key in seen:
            msg = f"duplicate pager key {key!r}"
            raise LayoutInvariantError(msg)
        seen.add(key)

        def anchor(ref: tuple[str, ...], prefix: tuple[str, ...] = region.source_prefix) -> str:
            return "/".join(quote(part, safe="") for part in ref[len(prefix) :])

        anchors: dict[str, int] = {}
        for page, sources in enumerate(region.anchors):
            for source in sources:
                anchors.setdefault(anchor(source.keys), page)
        order = tuple(anchor(ref.keys) for ref in region.source_order)
        cursor = MaterializedCursorRequest(key, len(region.cuts), fingerprint, anchors, region.owner, order)
        grant = coordinator.grant(cursor)
        position = grant.position
        if identity in options.pages:
            position = PagePosition(page=max(0, min(options.pages[identity], len(region.cuts) - 1)))
        coordinates[identity] = position.page
        requests[identity] = cursor
        positions[identity] = position
    if options.pages.keys() - allocation.regions.keys():
        msg = "page coordinates contain an unknown region occurrence"
        raise LayoutInvariantError(msg)
    visible = allocation.visible(coordinates)
    reports: list[RegionReport] = []
    retained: set[str] = set()
    for identity, region in allocation.regions.items():
        cursor = requests[identity]
        position = positions[identity]
        page_anchors = cursor.anchors or {}
        selected_anchor = position.anchor
        if selected_anchor is None or page_anchors.get(selected_anchor) != position.page:
            selected_anchor = next((key for key in cursor.source_order if page_anchors.get(key) == position.page), None)
        reports.append(
            RegionReport(region.cursor_key, region.allocation, len(region.cuts), position.page, selected_anchor)
        )
        if identity in visible:
            coordinator.record(cursor, position, anchor=selected_anchor)
        elif not isinstance(region.owner, Controlled | RoutedPage):
            retained.add(region.cursor_key)
    selected = allocation.select(coordinates)
    output = replace(selected.scene, pagers=coordinator.pagers)
    updates = list(coordinator.updates[:-1])
    active = coordinator.updates[-1]
    assert isinstance(active, ActivePagers)
    updates.append(ActivePagers(active.keys | retained))
    layout = RegionLayoutState(
        fingerprint,
        tuple(
            RegionAllocation(
                region.cursor_key,
                tuple((str(axis), value) for axis, value in region.allocation.values.items()),
                tuple(end - start for start, end in zip((0, *region.cuts[:-1]), region.cuts, strict=True)),
            )
            for region in allocation.regions.values()
        ),
    )
    updates.append(RegionLayoutUpdate(layout))
    updates.append(
        RetainedCompositionUpdate(
            RetainedComposition(document, _environment(request), replace(options, pages={}), result)
        )
    )

    def select_page(pages: Mapping[OccurrenceId, int]) -> PlanResult[BodyT]:
        return present_composition(result, rendered, request, replace(options, pages=pages))

    return replace(
        selected,
        page_selector=select_page,
        scene=output,
        allocation=allocation,
        report=replace(
            selected.report,
            search=result.report.search,
            regions=tuple(reports),
            scene_fingerprint=sha256(scene.Codec.dumps(output).encode()).hexdigest(),
        ),
        metrics=result.metrics,
        session_updates=tuple(updates) if request.session is not None else (),
    )

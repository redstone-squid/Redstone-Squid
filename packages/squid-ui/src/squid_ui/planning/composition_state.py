"""Reconcile certified pages with their owners, staging the displayed state for delivery."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Any
from urllib.parse import quote

from squid_ui import scene
from squid_ui.document import Document, DocumentLike, as_document
from squid_ui.errors import LayoutInvariantError
from squid_ui.ownership import Controlled
from squid_ui.pages import PagePosition
from squid_ui.planning.composition import OccurrenceId, SemanticOccurrence
from squid_ui.planning.composition_request import CompositionOptions
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.identity import has_routed_page, stable_fingerprint
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.pagination_content import TextPages
from squid_ui.planning.pagination_reuse import same_allocation_document
from squid_ui.planning.realizations import region_cursor_key
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanMetrics, PlanResult, PlanReuse, RegionReport
from squid_ui.planning.semantics import semantic_graph
from squid_ui.planning.structure import disclosure_state
from squid_ui.presentation import (
    ActivePagers,
    RegionAllocation,
    RegionLayoutState,
    RegionLayoutUpdate,
    RetainedCompositionUpdate,
)
from squid_ui.presentations import SemanticRef
from squid_ui.primitives.constraints import FIRST_PAGE, RoutedPage
from squid_ui.semantic import Details, Group, List, Navigation, Paged, Region


def _navigation_version(nav: PlannedNav | None) -> int | None:
    version = getattr(nav, "version", 0)
    return version if isinstance(version, int) else None


@dataclass(frozen=True, slots=True)
class RetainedComposition[BodyT: scene.Body = Any, RenderTargetT = Any, AdapterT = Any]:
    document: Document[RenderTargetT]
    request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    navigation_version: int | None
    options: CompositionOptions
    result: PlanResult[BodyT]

    def matches(
        self,
        document: Document[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions,
    ) -> bool:
        # Route factories may encode changed external state behind the same callable.
        return (
            not has_routed_page(document)
            and same_allocation_document(document, self.document)
            and request.target is self.request.target
            and request.chrome is self.request.chrome
            and request.localization is self.request.localization
            and request.palette is self.request.palette
            and request.reservation == self.request.reservation
            and request.strict == self.request.strict
            and request.nav is self.request.nav
            and self.navigation_version is not None
            and _navigation_version(request.nav) == self.navigation_version
            and request.search_budget == self.request.search_budget
            and request.shared_pagination == self.request.shared_pagination
            and request.presentation is self.request.presentation
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
    graph = semantic_graph(Group(document.children, key=document.key or "@document"))
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
                if not source.keys[-1].startswith("@"):
                    anchors.setdefault(anchor(source.keys), page)
        order = tuple(anchor(ref.keys) for ref in region.source_order if not ref.keys[-1].startswith("@"))
        authored = graph.units.get(SemanticRef(region.source_prefix))
        owner = (
            authored.value.page
            if authored is not None and isinstance(authored.value, Region | Paged | List | TextPages)
            else region.owner
        )
        content = (
            replace(authored.value, page=FIRST_PAGE)
            if authored is not None and isinstance(authored.value, Region | Paged | List | TextPages)
            else None
        )
        cursor_fingerprint = stable_fingerprint((content, region.cuts)) if content is not None else fingerprint
        if not isinstance(owner, Controlled | RoutedPage):
            stored = request.presentation.cursor(key)
            if stored.fingerprint == cursor_fingerprint and 0 <= stored.position.page < len(region.cuts):
                covered = {anchor(ref.keys) for ref in region.anchors[stored.position.page]}
                if stored.position.anchor is not None and stored.position.anchor in covered:
                    anchors[stored.position.anchor] = stored.position.page
        cursor = MaterializedCursorRequest(key, len(region.cuts), cursor_fingerprint, anchors, owner, order)
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
        elif not isinstance(cursor.owner, Controlled | RoutedPage):
            retained.add(region.cursor_key)
    selected = allocation.select(coordinates)
    output = replace(selected.scene, pagers=coordinator.pagers)
    updates = list(coordinator.updates[:-1])
    active = coordinator.updates[-1]
    assert isinstance(active, ActivePagers)
    # A closed disclosure hides its layout scopes without deleting their delivered cursors.
    # Hidden pagers receive no new position or allocation until the reader opens them.
    for reference, unit in graph.units.items():
        if not isinstance(unit.value, Region | Paged | List | Navigation | TextPages):
            continue
        parent = unit.parent
        hidden = nested_region = False
        while parent is not None:
            ancestor = graph.units[parent]
            hidden |= isinstance(ancestor.value, Details) and not disclosure_state(ancestor.value, request.presentation)
            nested_region |= isinstance(ancestor.value, Region)
            parent = ancestor.parent
        if hidden:
            key = (
                region_cursor_key(SemanticOccurrence(OccurrenceId(reference.keys), graph, reference))
                if isinstance(unit.value, Region) or (isinstance(unit.value, Paged) and nested_region)
                else f"{unit.value.key}.destinations"
                if isinstance(unit.value, Navigation)
                else unit.value.key
            )
            if key in request.presentation.cursors:
                retained.add(key)
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
            RetainedComposition(document, request, _navigation_version(request.nav), replace(options, pages={}), result)
        )
    )

    def select_page(pages: Mapping[OccurrenceId, int]) -> PlanResult[BodyT]:
        reused = replace(result, metrics=PlanMetrics(cache_hit=True, reuse=PlanReuse.ALLOCATION))
        return present_composition(reused, rendered, request, replace(options, pages=pages))

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
        session_updates=tuple(updates) if request.session is not None or request.shared_pagination else (),
    )

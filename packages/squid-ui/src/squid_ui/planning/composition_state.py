"""Reconcile certified pages with their owners, staging the displayed state for delivery."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import partial
from hashlib import sha256
from types import MappingProxyType
from typing import Any
from urllib.parse import quote

from squid_ui import scene
from squid_ui.authoring.document import Document
from squid_ui.authoring.presentations import SemanticRef
from squid_ui.authoring.semantic import Details, Grid, Group, Items, List, Navigation, Paged, Region
from squid_ui.errors import LayoutInvariantError
from squid_ui.interactions.ownership import Controlled
from squid_ui.layout import Known, RegionRef
from squid_ui.native import NativeTraits, NativeValue
from squid_ui.overflow import FIRST_PAGE, RoutedPage
from squid_ui.pages import PagePosition
from squid_ui.planning.action_projection import prepare_action_allocation
from squid_ui.planning.composition import OccurrenceId, SemanticOccurrence
from squid_ui.planning.composition_request import CompositionOptions
from squid_ui.planning.identity import requires_live_preparation, stable_fingerprint
from squid_ui.planning.interaction_semantics import ItemSummary
from squid_ui.planning.offers import region_cursor_key
from squid_ui.planning.pagination.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.pagination.reuse import same_allocation_document
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanMetrics, PlanResult, PlanReuse, RegionReport
from squid_ui.planning.semantic_index import ItemBranch, SemanticGraph, semantic_graph
from squid_ui.planning.structure import disclosure_state, opened_item
from squid_ui.presentation_state import (
    ActivePagers,
    RegionAllocation,
    RegionLayoutState,
    RegionLayoutUpdate,
    RetainedCompositionUpdate,
)


@dataclass(frozen=True, slots=True)
class RetainedComposition[BodyT: scene.Body = Any, RenderTargetT = Any, AdapterT = Any]:
    """One certified allocation and the authored structure needed to present its pages.

    The request retains the live presentation session so page callbacks resolve current
    cursors. Graph and fingerprint describe the authored snapshot, not that mutable state.
    Delivery installs this owner on the session; failed delivery leaves the old owner intact.
    Use prepare() for a different document; replacing only the result preserves its graph and fingerprint.
    """

    document: Document[RenderTargetT]
    request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    environment: tuple[object, ...]
    options: CompositionOptions
    result: PlanResult[BodyT]
    graph: SemanticGraph[RenderTargetT, Any] = field(repr=False, compare=False)
    fingerprint: str
    _cursor_fingerprints: dict[OccurrenceId, str] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def prepare(
        cls,
        document: Document[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions,
        result: PlanResult[BodyT],
    ) -> RetainedComposition[BodyT, RenderTargetT, AdapterT]:
        """Capture allocation inputs, excluding the transient page coordinates."""
        # Preparation belongs to the authored snapshot. Replacing its result must
        # retain these values rather than traverse and fingerprint the tree again.
        graph = semantic_graph(Group(document.children, key=document.key or "@document"))
        fingerprint = stable_fingerprint((document,), cache=request.fingerprints)
        if request.session is not None and result.allocation is not None:
            frontend = request.target.adapter.frontend or request.target.adapter.name
            allocation = prepare_action_allocation(result.allocation, graph, frontend, request.presentation.declared)
            # The envelope supplies diagnostics; selected pages supply live bindings.
            result = replace(
                result,
                bindings=MappingProxyType({}),
                form_bindings=MappingProxyType({}),
                form_plans=MappingProxyType({}),
                resources=MappingProxyType({}),
                allocation=allocation,
            )
        return cls(document, request, request.allocation_key(), replace(options, pages={}), result, graph, fingerprint)

    def reuse(
        self,
        document: Document[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions,
    ) -> PlanResult[BodyT]:
        """Present matching certified cuts with the current authored ownership values."""
        # Controlled coordinates do not invalidate cuts, but their new values must
        # participate in cursor resolution. Unchanged snapshots keep the prepared graph.
        owner = self if document == self.document else self.prepare(document, request, options, self.result)
        return owner.present(options.pages, request=request, options=options)

    def matches(
        self,
        document: Document[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions,
    ) -> bool:
        # Opaque factories may encode changed external state behind the same callable.
        return (
            not requires_live_preparation(document)
            and same_allocation_document(document, self.document)
            and request.allocation_key() == self.environment
            and isinstance(getattr(request.nav, "version", 0), int)
            and request.presentation is self.request.presentation
            and replace(options, pages={}) == self.options
        )

    def present(
        self,
        pages: Mapping[OccurrenceId, int] | None = None,
        *,
        request: PlanRequest[BodyT, RenderTargetT, AdapterT] | None = None,
        options: CompositionOptions | None = None,
    ) -> PlanResult[BodyT]:
        """Select certified output against current cursors without rebuilding authored structure."""
        request = self.request if request is None else request
        options = self.options if options is None else options
        result = self.result
        if pages is not None:
            options = replace(options, pages=pages)
            result = replace(result, metrics=PlanMetrics(cache_hit=True, reuse=PlanReuse.ALLOCATION))
        allocation = result.allocation
        assert allocation is not None
        fingerprint = self.fingerprint
        graph = self.graph
        overrides = {**request.presentation.pending_positions, **(request.positions or {})}
        coordinator = CursorCoordinator(request.presentation, overrides=overrides)
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

            def keyed(ref: SemanticRef) -> bool:
                unit = graph.units.get(ref)
                return not ref.keys[-1].startswith("@") or (unit is not None and isinstance(unit.value, ItemSummary))

            def anchor(ref: tuple[str, ...], prefix: tuple[str, ...] = region.source_prefix) -> str:
                unit = graph.units.get(SemanticRef(ref))
                if unit is not None and isinstance(unit.value, ItemSummary):
                    ref = ref[:-1]
                return "/".join(quote(part, safe="") for part in ref[len(prefix) :])

            anchors: dict[str, int] = {}
            for page, sources in enumerate(region.anchors):
                for source in sources:
                    if keyed(source):
                        anchors.setdefault(anchor(source.keys), page)
            order = tuple(anchor(ref.keys) for ref in region.source_order if keyed(ref))
            authored = graph.units.get(SemanticRef(region.source_prefix))
            owner = (
                authored.value.page
                if authored is not None and isinstance(authored.value, Region | Paged | List)
                else region.owner
            )
            content = (
                replace(authored.value, page=FIRST_PAGE)
                if authored is not None and isinstance(authored.value, Region | Paged | List)
                else None
            )
            if (
                authored is not None
                and isinstance(authored.value, NativeValue)
                and (pagination := NativeTraits.of(authored.value).pagination) is not None
            ):
                owner = pagination.page
                content = authored.value.semantics.with_pagination(authored.value, replace(pagination, page=FIRST_PAGE))
            if authored is not None and isinstance(authored.value, Grid):
                content = authored.value
            cursor_fingerprint = self._cursor_fingerprints.get(identity)
            if cursor_fingerprint is None:
                # Content and cuts belong to this retained snapshot; page coordinates
                # and delivery state do not affect the cursor's content identity.
                cursor_fingerprint = (
                    stable_fingerprint((content, region.cuts), cache=request.fingerprints)
                    if content is not None
                    else fingerprint
                )
                self._cursor_fingerprints[identity] = cursor_fingerprint
            if not isinstance(owner, Controlled | RoutedPage):
                stored = request.presentation.cursor(key)
                if stored.fingerprint == cursor_fingerprint and 0 <= stored.position.page < len(region.cuts):
                    covered = {anchor(ref.keys) for ref in region.anchors[stored.position.page]}
                    if stored.position.anchor is not None and stored.position.anchor in covered:
                        anchors[stored.position.anchor] = stored.position.page
            cursor = MaterializedCursorRequest(key, len(region.cuts), cursor_fingerprint, anchors, owner, order)
            position = coordinator.grant(cursor)
            if identity in options.pages:
                position = PagePosition(page=max(0, min(options.pages[identity], len(region.cuts) - 1)))
            coordinates[identity] = position.page
            requests[identity] = cursor
            positions[identity] = position
        if options.pages.keys() - allocation.regions.keys():
            msg = "page coordinates contain an unknown region occurrence"
            raise LayoutInvariantError(msg)
        visible = allocation.visible(coordinates)
        selected = allocation.select(coordinates)
        reports: list[RegionReport] = []
        retained: set[str] = set()
        for identity, region in allocation.regions.items():
            cursor = requests[identity]
            position = positions[identity]
            page_anchors = cursor.anchors or {}
            selected_anchor = position.anchor
            if selected_anchor is None or page_anchors.get(selected_anchor) != position.page:
                selected_anchor = next(
                    (key for key in cursor.source_order if page_anchors.get(key) == position.page), None
                )
            reports.append(
                RegionReport(region.cursor_key, region.allocation, len(region.cuts), position.page, selected_anchor)
            )
            if identity in visible:
                count = (
                    selected.layout.region(RegionRef(region.source_prefix)).count
                    if selected.layout is not None
                    else Known(len(region.cuts))
                )
                coordinator.record(cursor, position, anchor=selected_anchor, count=count)
            elif not isinstance(cursor.owner, Controlled | RoutedPage):
                retained.add(region.cursor_key)
        output = replace(selected.scene, pagers=coordinator.pagers)
        updates = list(coordinator.updates[:-1])
        active = coordinator.updates[-1]
        assert isinstance(active, ActivePagers)
        # A closed disclosure hides its layout scopes without deleting their delivered cursors.
        # Hidden pagers receive no new position or allocation until the reader opens them.
        for reference, unit in graph.units.items():
            if isinstance(unit.value, Items):
                keys = (
                    f"{unit.value.key}.items",
                    *(f"{unit.value.key}.{item.key}.detail" for item in unit.value.items),
                )
                retained.update(key for key in keys if key in request.presentation.cursors)
            if not isinstance(unit.value, Region | Paged | List | Navigation | Grid) and not (
                NativeTraits.of(unit.value).pagination is not None
            ):
                continue
            parent = unit.parent
            hidden = nested_region = False
            while parent is not None:
                ancestor = graph.units[parent]
                hidden |= isinstance(ancestor.value, Details) and not disclosure_state(
                    ancestor.value, request.presentation
                )
                if isinstance(ancestor.value, ItemBranch):
                    hidden |= opened_item(ancestor.value.items, request.presentation) != ancestor.value.item.key
                nested_region |= isinstance(ancestor.value, Region)
                parent = ancestor.parent
            if hidden:
                if (pagination := NativeTraits.of(unit.value).pagination) is not None:
                    key = pagination.key or region_cursor_key(
                        SemanticOccurrence(OccurrenceId(reference.keys), graph, reference)
                    )
                else:
                    key = (
                        region_cursor_key(SemanticOccurrence(OccurrenceId(reference.keys), graph, reference))
                        if isinstance(unit.value, Region) or (isinstance(unit.value, Paged) and nested_region)
                        else f"{unit.value.key}.destinations"
                        if isinstance(unit.value, Navigation)
                        else f"{unit.value.key}.cells"
                        if isinstance(unit.value, Grid)
                        else unit.value.key
                        if isinstance(unit.value, Paged | List)
                        else ""
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
        if not request.partial_layout:
            updates.append(RetainedCompositionUpdate(self))

        return replace(
            selected,
            page_selector=partial(self.present, request=request, options=options),
            scene=output,
            allocation=allocation,
            report=replace(
                selected.report,
                search=result.report.search,
                regions=tuple(reports),
                scene_fingerprint=sha256(scene.Codec.dumps(output).encode()).hexdigest(),
            ),
            metrics=result.metrics,
            session_updates=tuple(updates) if request.session is not None or request.composition is None else (),
        )

"""Allocate persistent and pageable regions using bottom-up resource frontiers.

Each candidate carries an envelope that bounds every one of its pages. Parents
combine envelopes, so turning a page never borrows resources from a sibling.
Only the selected candidate is materialized into live handlers and cursor writes.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from itertools import product
from typing import Any
from urllib.parse import quote

from squid_ui import scene
from squid_ui.document import Document
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.ownership import Controlled
from squid_ui.pages import PagePosition
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.planning.identity import stable_fingerprint, stable_value
from squid_ui.planning.navigation import PagerNav
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import EMPTY_COST, Axis, ResourceCost
from squid_ui.planning.result import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanSeverity, RegionReport
from squid_ui.presentation import ActivePagers, CursorUpdate, RegionAllocation, RegionLayoutState, RegionLayoutUpdate
from squid_ui.primitives.constraints import RoutedPage
from squid_ui.semantic import (
    Article,
    Aside,
    Block,
    Budgeted,
    FallbackContent,
    Group,
    KeepWithNext,
    Paged,
    Region,
    Section,
    Stack,
    Unbreakable,
)
from squid_ui.text import Text


@dataclass(frozen=True, slots=True)
class _Layout:
    cost: ResourceCost
    loss: DegradationProfile = DegradationProfile()
    turns: int = 0
    preference: int = field(default=0, kw_only=True)
    events: tuple[PlanEvent, ...] = field(default=(), kw_only=True)
    parts: tuple[_Layout, ...] = ()
    leaf: PlanResult[Any] | None = None
    shell: PlanResult[Any] | None = None
    region: Region[Any, Any] | None = None
    key: str = ""
    pages: tuple[tuple[_Layout, ...], ...] = ()
    anchors: tuple[tuple[str, ...], ...] = ()
    header: _Layout | None = None
    footer: _Layout | None = None
    navigation: tuple[PlanResult[Any], ...] = ()
    local_pagers: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class _Item:
    node: Any
    path: str
    anchors: tuple[str, ...] = ()
    keep_with_next: bool = False


def _items(nodes: Sequence[Any], path: str, prefix: str = "") -> list[_Item]:
    items: list[_Item] = []
    seen: set[str] = set()
    for index, node in enumerate(nodes):
        key = _key(node)
        if key:
            if key in seen:
                message = f"{path}: duplicate sibling key {key!r}"
                raise LayoutInvariantError(message)
            seen.add(key)
        suffix = quote(str(key), safe="") if key else f"@{index}"
        child_path = f"{path}/{suffix}"
        anchor = f"{prefix}{suffix}" if key else None
        content = node.node if isinstance(node, KeepWithNext) else node
        if isinstance(content, Group | Stack):
            children = _items(content.children, child_path, f"{prefix}{suffix}/")
            if children and anchor is not None:
                children[0] = replace(children[0], anchors=(anchor, *children[0].anchors))
            if children and isinstance(node, KeepWithNext):
                children[-1] = replace(children[-1], keep_with_next=True)
            items.extend(children)
        else:
            items.append(
                _Item(node, child_path, (anchor,) if anchor is not None else (), isinstance(node, KeepWithNext))
            )
    return items


def _envelope(left: ResourceCost, right: ResourceCost) -> ResourceCost:
    return ResourceCost({axis: max(left.get(axis), right.get(axis)) for axis in left.axes + right.axes})


def _dominates(left: _Layout, right: _Layout) -> bool:
    return (
        left.loss <= right.loss
        and left.turns <= right.turns
        and left.preference <= right.preference
        and all(left.cost.get(axis) <= right.cost.get(axis) for axis in left.cost.axes + right.cost.axes)
    )


def _frontier(candidates: Sequence[_Layout]) -> tuple[_Layout, ...]:
    kept: list[_Layout] = []
    for candidate in candidates:
        if any(_dominates(other, candidate) for other in kept):
            continue
        kept = [other for other in kept if not _dominates(candidate, other)]
        kept.append(candidate)
    return tuple(kept)


def _key(node: Any) -> str | None:
    while isinstance(node, Unbreakable | KeepWithNext | Budgeted | FallbackContent):
        node = node.primary if isinstance(node, FallbackContent) else node.node
    return getattr(node, "key", None)


def contains_regions(nodes: Sequence[Any]) -> bool:
    for node in nodes:
        if isinstance(node, Region):
            return True
        for name in ("children", "header", "footer", "alternates"):
            children = getattr(node, name, ())
            if isinstance(children, tuple) and contains_regions(children):
                return True
        for name in ("node", "primary"):
            child = getattr(node, name, None)
            if child is not None and contains_regions((child,)):
                return True
    return False


def _probe_pages(value: Any, positions: dict[str, PagePosition]) -> Any:
    """Override controlled pages only in discarded measurement probes."""
    if isinstance(value, tuple):
        items = tuple(_probe_pages(item, positions) for item in value)
        return value if all(left is right for left, right in zip(value, items, strict=True)) else items
    if not is_dataclass(value) or isinstance(value, type):
        return value
    changes: dict[str, Any] = {}
    for descriptor in fields(value):
        if not descriptor.init:
            continue
        original = getattr(value, descriptor.name)
        updated = _probe_pages(original, positions)
        if updated is not original:
            changes[descriptor.name] = updated
    key = getattr(value, "key", None)
    owner = getattr(value, "page", None)
    if key in positions and isinstance(owner, Controlled | RoutedPage):
        changes["page"] = replace(owner, value=positions[key].page)
    return replace(value, **changes) if changes else value


def _layout_value(value: Any) -> Any:
    if isinstance(value, dict):
        if value.get("type") in ("Region", "Paged", "Paginate", "List"):
            value = {**value, "fields": {key: item for key, item in value["fields"].items() if key != "page"}}
        return {key: _layout_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_layout_value(item) for item in value]
    return value


class _Solver:
    def __init__(self, request: PlanRequest[Any, Any, Any], document: Document[Any, Any]) -> None:
        self.request = request
        self.document_cost = ResourceCost({Axis.ATTACHMENTS: len({asset.key for asset in document.assets})})
        if not self.document_cost.within(request.target.reserve(request.reservation).capacities):
            message = "persistent document attachments exceed the target budget"
            raise UnsolvableLayoutError(message, context={"reason": "persistent_attachments"})
        self.target = request.target.reserve(request.reservation + self.document_cost)
        self.remaining = request.search_budget**2
        self.measurements = 0
        self.single_page = False
        self.root_region = (
            f"$/{quote(document.children[0].key, safe='')}"
            if len(document.children) == 1 and isinstance(document.children[0], Region)
            else None
        )
        self.empty: PlanResult[Any] | None = None
        self.evaluations = 0
        environment = dict(request.cache_context(target=self.target, chrome=request.chrome))
        environment.pop("presentation", None)
        environment.pop("positions", None)
        environment.pop("search_budget", None)
        self.fingerprint = stable_fingerprint(
            (
                _layout_value(stable_value(document)),
                environment,
                request.presentation.selections,
                request.presentation.disclosures,
                request.presentation.toggles,
            )
        )
        previous = request.presentation.region_layout
        self.previous = (
            {item.key: item for item in previous.allocations}
            if previous is not None and previous.fingerprint == self.fingerprint
            else {}
        )
        self.compiled: dict[int, tuple[Any, tuple[_Layout, ...]]] = {}

    def spend(self) -> None:
        if self.remaining <= 0:
            msg = "region allocation exhausted its search budget before completing the frontier"
            raise LayoutSearchExhaustedError(msg)
        self.remaining -= 1
        self.evaluations += 1

    def compile(self, nodes: tuple[Any, ...], *, target: Any = None, positions: Any = None) -> PlanResult[Any] | None:
        if not nodes and self.empty is not None:
            return self.empty
        self.spend()
        if self.measurements >= self.request.search_budget:
            message = "region allocation exhausted its measurement budget"
            raise LayoutSearchExhaustedError(message)
        self.measurements += 1
        try:
            planned = self.target.dialect.planner.plan(
                Document(nodes),
                replace(
                    self.request,
                    target=self.target if target is None else target,
                    reservation=EMPTY_COST,
                    strict=False,
                    positions=self.request.positions if positions is None else positions,
                ),
                cache=None,
                memo=None,
            )
        except UnsolvableLayoutError as error:
            if error.context.get("search_exhausted"):
                message = "a region representation search ended without proving feasibility"
                raise LayoutSearchExhaustedError(message) from error
            return None
        cost = _envelope(planned.cost, ResourceCost({Axis.ATTACHMENTS: len(planned.scene.assets)}))
        measured_target = self.target if target is None else target
        if not cost.within(measured_target.capacities):
            return None
        result = replace(planned, cost=cost)
        if not nodes:
            self.empty = result
        return result

    def combine(self, options: Sequence[tuple[_Layout, ...]]) -> tuple[_Layout, ...]:
        frontier = (_Layout(EMPTY_COST),)
        for children in options:
            combined: list[_Layout] = []
            for left, right in product(frontier, children):
                self.spend()
                cost = left.cost + right.cost
                if cost.within(self.target.capacities):
                    combined.append(
                        _Layout(
                            cost,
                            left.loss.merged(right.loss),
                            left.turns + right.turns,
                            (*left.parts, right),
                            preference=left.preference + right.preference,
                        )
                    )
            frontier = _frontier(combined)
        return frontier

    def sequence(self, nodes: Sequence[Any], path: str) -> tuple[_Layout, ...]:
        return self.combine(tuple(self.node(item.node, item.path) for item in _items(nodes, path)))

    def local_envelope(
        self,
        node: Any,
        planned: PlanResult[Any],
        target: Any,
    ) -> tuple[ResourceCost, int, frozenset[str], DegradationProfile] | None:
        extents = {pager.key: pager.pages for pager in planned.scene.pagers}
        visited: set[tuple[tuple[str, int], ...]] = set()
        cost = planned.cost
        degradation = planned.degradation
        while extents:
            keys = sorted(extents)
            fresh = False
            for coordinates in product(*(range(extents[key]) for key in keys)):
                values = tuple(zip(keys, coordinates, strict=True))
                if values in visited:
                    continue
                fresh = True
                self.spend()
                visited.add(values)
                positions = {
                    **(self.request.positions or {}),
                    **{key: PagePosition(page=index) for key, index in values},
                }
                probe = self.compile((_probe_pages(node, positions),), target=target, positions=positions)
                if probe is None:
                    return None
                cost = _envelope(cost, probe.cost)
                degradation = max(degradation, probe.degradation)
                for pager in probe.scene.pagers:
                    extents[pager.key] = max(extents.get(pager.key, 0), pager.pages)
            if not fresh:
                break
        return cost, sum(pages - 1 for pages in extents.values()), frozenset(extents), degradation

    def node(self, node: Any, path: str) -> tuple[_Layout, ...]:
        if isinstance(node, Budgeted) and isinstance(node.node, Paged):
            return self.paged(node.node, path, minimum=node.minimum)
        if isinstance(node, Paged):
            return self.paged(node, path)
        if isinstance(node, Section | Block | Article | Aside) and contains_regions(node.children):
            return self.container(node, self.sequence(node.children, path))
        if isinstance(node, Region):
            return self.region(node, path)
        if isinstance(node, Group | Stack):
            return self.sequence(node.children, path)
        if isinstance(node, FallbackContent):
            choices: list[_Layout] = []
            for index, alternate in enumerate((node.primary, *node.alternates)):
                choices.extend(
                    replace(
                        item,
                        loss=item.loss.with_effect(DegradationEffect(0, path, semantic_steps=index))
                        if index
                        else item.loss,
                        events=(
                            *item.events,
                            PlanEvent(
                                "layout.semantic_fallback",
                                path,
                                f"Selected authored representation {index + 1}",
                                severity=PlanSeverity.DEGRADATION,
                                after={"rung": index},
                            ),
                        )
                        if index
                        else item.events,
                    )
                    for item in self.node(alternate, path)
                )
            return _frontier(choices)
        if isinstance(node, Unbreakable | KeepWithNext):
            return self.node(node.node, path)
        if contains_regions((node,)):
            msg = f"{path}: this wrapper does not support nested regions"
            raise LayoutInvariantError(msg)
        cached = self.compiled.get(id(node))
        if cached is not None:
            return cached[1]
        # A cheaper representation is revealed by withholding one unit below a
        # measured cost. Recursing on each axis visits the adaptation boundaries,
        # rather than sampling arbitrary component or text grants.
        pending = [self.target]
        visited: set[tuple[tuple[Any, int], ...]] = set()
        results: list[_Layout] = []
        preferred_scene: scene.Scene[Any] | None = None
        while pending:
            candidate_target = pending.pop()
            identity = tuple(candidate_target.capacities.items())
            if identity in visited:
                continue
            visited.add(identity)
            planned = self.compile((node,), target=candidate_target)
            if planned is None:
                continue
            if preferred_scene is None:
                preferred_scene = planned.scene
            envelope = self.local_envelope(node, planned, candidate_target)
            if envelope is not None:
                cost, turns, pagers, degradation = envelope
                results.append(
                    _Layout(
                        cost,
                        loss=degradation,
                        leaf=planned,
                        turns=turns,
                        preference=int(planned.scene != preferred_scene),
                        local_pagers=pagers,
                    )
                )
            for axis, capacity in candidate_target.capacities.items():
                spent = (planned.cost if envelope is None else envelope[0]).get(axis)
                if spent:
                    pending.append(candidate_target.reserve(ResourceCost({axis: capacity - spent + 1})))
        frontier = _frontier(results)
        self.compiled[id(node)] = (node, frontier)
        return frontier

    def container(self, node: Any, candidates: tuple[_Layout, ...]) -> tuple[_Layout, ...]:
        shell = self.compile((replace(node, children=()),))
        if shell is None:
            return ()
        body = shell.scene.components_v2.children
        if len(body) != 1 or not isinstance(body[0], scene.Panel):
            message = f"{type(node).__name__} cannot contain independently allocated regions"
            raise LayoutInvariantError(message)
        return tuple(
            _Layout(
                item.cost + shell.cost,
                loss=item.loss,
                turns=item.turns,
                parts=(item,),
                shell=shell,
                preference=item.preference,
            )
            for item in candidates
            if (item.cost + shell.cost).within(self.target.capacities)
        )

    def paged(self, node: Paged, path: str, *, minimum: int = 0) -> tuple[_Layout, ...]:
        content = node.node
        children = (
            content.children if isinstance(content, Group | Stack | Section | Block | Article | Aside) else (content,)
        )
        region = Region(tuple(children), node.key, "", page=node.page)
        candidates = self.region(region, path, chars=node.chars, minimum=minimum, page_footer=node.footer)
        if isinstance(content, Section | Block | Article | Aside):
            return self.container(content, candidates)
        return candidates

    def region(
        self,
        region: Region[Any, Any],
        path: str,
        *,
        chars: int | None = None,
        minimum: int = 0,
        page_footer: Any = None,
    ) -> tuple[_Layout, ...]:
        groups: list[tuple[_Item, ...]] = []
        current: list[_Item] = []
        for item in _items(region.children, path):
            current.append(item)
            if not item.keep_with_next:
                groups.append(tuple(current))
                current = []
        if current:
            if groups:
                groups[-1] = (*groups[-1], *current)
            else:
                groups.append(tuple(current))
        if not groups:
            groups = [()]
        anchors = [tuple(anchor for item in group for anchor in item.anchors) for group in groups]
        options = [self.combine(tuple(self.node(item.node, item.path) for item in group)) for group in groups]
        headers = self.sequence(region.header, f"{path}/@header")
        footers = self.sequence(region.footer, f"{path}/@footer")
        results: list[_Layout] = []
        chrome = replace(
            self.request.chrome,
            previous=Text(t"{self.request.chrome.previous} — {region.title}"),
            next=Text(t"{self.request.chrome.next} — {region.title}"),
        )
        if not region.title:
            chrome = self.request.chrome
        if page_footer is not None:
            chrome = replace(chrome, page_footer=page_footer)
        nav = None if self.request.nav is None else PagerNav(self.request.nav, self.request.presentation)
        broker = CursorCoordinator(self.request.presentation, chrome, nav)
        previous = self.previous.get(path)
        extents = (len(previous.page_sizes),) if previous is not None else range(1, len(groups) + 1)
        if self.single_page:
            extents = (1,)
        for extent in extents:
            # At the document root, future page counts cannot beat an already
            # feasible lossless plan once their navigation lower bound is worse.
            lossless_turns = [item.turns for item in results if item.loss.lossless]
            if path == self.root_region and lossless_turns and extent - 1 > min(lossless_turns):
                break
            if extent > 1 and nav is None:
                break
            navigation = tuple(
                self.compile(tuple(broker.controls(path, PagePosition(page=index), extent, region.page)))
                for index in range(extent)
            )
            if any(item is None for item in navigation):
                continue
            navs = tuple(item for item in navigation if item is not None)
            for header, footer in product(headers, footers):
                states: dict[int, tuple[_Layout, ...]] = {0: (_Layout(EMPTY_COST),)}
                for page_index in range(extent):
                    following: dict[int, list[_Layout]] = {}
                    for start, prefixes in states.items():
                        segments = (_Layout(EMPTY_COST),)
                        ends = range(start + 1, len(groups) - (extent - page_index - 1) + 1)
                        for end in ends:
                            combined: list[_Layout] = []
                            for left, right in product(segments, options[end - 1]):
                                self.spend()
                                cost = left.cost + right.cost
                                if cost.within(self.target.capacities):
                                    combined.append(
                                        _Layout(
                                            cost,
                                            left.loss.merged(right.loss),
                                            left.turns + right.turns,
                                            (*left.parts, right),
                                            preference=left.preference + right.preference,
                                        )
                                    )
                            segments = _frontier(combined)
                            if previous is not None and end != start + previous.page_sizes[page_index]:
                                continue
                            for prefix, segment in product(prefixes, segments):
                                self.spend()
                                if chars is not None and segment.cost.get(Axis.DISPLAY_TEXT) > chars:
                                    continue
                                body_cost = _envelope(segment.cost, ResourceCost({Axis.DISPLAY_TEXT: minimum}))
                                cost = header.cost + body_cost + navs[page_index].cost + footer.cost
                                if not cost.within(self.target.capacities):
                                    continue
                                candidate = _Layout(
                                    _envelope(prefix.cost, cost),
                                    prefix.loss.merged(segment.loss),
                                    prefix.turns + segment.turns,
                                    preference=prefix.preference + segment.preference,
                                    pages=(*prefix.pages, segment.parts),
                                    anchors=(
                                        *prefix.anchors,
                                        tuple(key for keys in anchors[start:end] for key in keys),
                                    ),
                                )
                                following.setdefault(end, []).append(candidate)
                    states = {end: _frontier(items) for end, items in following.items()}
                results.extend(
                    replace(
                        item,
                        region=region,
                        key=path,
                        header=header,
                        footer=footer,
                        navigation=navs,
                        loss=item.loss.merged(header.loss).merged(footer.loss),
                        turns=item.turns + extent - 1 + header.turns + footer.turns,
                        preference=item.preference + header.preference + footer.preference,
                    )
                    for item in states.get(len(groups), ())
                )
            results = list(_frontier(results))
        previous = self.previous.get(path)
        if previous is not None:
            allocation = ResourceCost({Axis(axis): value for axis, value in previous.costs})
            results = [
                replace(item, cost=allocation)
                for item in results
                if tuple(len(page) for page in item.pages) == previous.page_sizes
                and all(item.cost.get(axis) <= allocation.get(axis) for axis in item.cost.axes + allocation.axes)
            ]
        return tuple(results)


def plan_regions(document: Document[Any, Any], request: PlanRequest[Any, Any, Any]) -> PlanResult[Any]:
    if request.target.body_type is not scene.ComponentsV2:
        msg = "joint regions currently require a Components V2 target"
        raise LayoutInvariantError(msg)
    solver = _Solver(request, document)
    solver.single_page = not solver.previous
    candidates = solver.sequence(document.children, "$")
    if solver.single_page and not any(item.loss.lossless for item in candidates):
        solver.single_page = False
        candidates = solver.sequence(document.children, "$")
    if not candidates:
        msg = "no region allocation fits the target"
        raise UnsolvableLayoutError(
            msg,
            context={
                "reason": "region_allocation",
                "document_key": document.key,
                "capacities": dict(solver.target.capacities),
            },
        )
    selected = min(
        candidates, key=lambda item: (item.loss, item.turns, item.preference, tuple(item.cost.values.items()))
    )
    if request.strict and not selected.loss.lossless:
        message = "joint region allocation requires an authored degradation"
        raise LayoutDegradedError(message)
    broker = CursorCoordinator(
        request.presentation,
        request.chrome,
        None if request.nav is None else PagerNav(request.nav, request.presentation),
        request.positions,
    )
    leaves: list[PlanResult[Any]] = []
    reports: list[RegionReport] = []
    allocations: list[RegionAllocation] = []
    retained: set[str] = set()
    events: list[PlanEvent] = []

    def draw(item: _Layout, *, visible: bool = True) -> tuple[scene.Node, ...]:
        events.extend(item.events)
        if item.leaf is not None:
            if visible:
                leaves.append(item.leaf)
                return item.leaf.scene.components_v2.children
            retained.update(item.local_pagers)
            return ()
        children: list[scene.Node] = []
        if item.region is None:
            for part in item.parts:
                children.extend(draw(part, visible=visible))
        else:
            extent = len(item.pages)
            allocations.append(
                RegionAllocation(
                    item.key,
                    tuple((str(axis), value) for axis, value in item.cost.values.items()),
                    tuple(len(page) for page in item.pages),
                )
            )
            anchors = {key: index for index, keys in enumerate(item.anchors) for key in keys}
            cursor = MaterializedCursorRequest(
                item.key, extent, stable_fingerprint((item.region,)), anchors, item.region.page
            )
            grant = broker.grant(cursor)
            keys = item.anchors[grant.position.page]
            anchor = grant.position.anchor if grant.position.anchor in keys else next(iter(keys), None)
            reports.append(RegionReport(item.key, item.cost, extent, grant.position.page, anchor))
            if visible:
                broker.record(cursor, grant.position, anchor=anchor)
            else:
                retained.add(item.key)
            if item.header is not None:
                children.extend(draw(item.header, visible=visible))
            for index, page in enumerate(item.pages):
                for part in page:
                    children.extend(draw(part, visible=visible and index == grant.position.page))
            if visible:
                navigation = item.navigation[grant.position.page]
                leaves.append(navigation)
                children.extend(navigation.scene.components_v2.children)
            if item.footer is not None:
                children.extend(draw(item.footer, visible=visible))
        if item.shell is not None and visible:
            leaves.append(item.shell)
            shell = item.shell.scene.components_v2.children[0]
            assert isinstance(shell, scene.Panel)
            return (replace(shell, children=(*shell.children, *children)),)
        return tuple(children)

    children = draw(selected)
    bindings: dict[str, Any] = {}
    forms: dict[str, Any] = {}
    resources: dict[str, Any] = {}
    updates: list[Any] = []
    assets: dict[str, Any] = {}
    for leaf in leaves:
        for key in leaf.bindings:
            if key in bindings:
                msg = f"duplicate visible action key {key!r}"
                raise LayoutInvariantError(msg)
        bindings.update(leaf.bindings)
        for key in leaf.form_bindings:
            if key in forms:
                message = f"duplicate visible form key {key!r}"
                raise LayoutInvariantError(message)
        forms.update(leaf.form_bindings)
        for key, resource in leaf.resources.items():
            if key in resources and resources[key] != resource:
                message = f"conflicting resource key {key!r} across regions"
                raise LayoutInvariantError(message)
            resources[key] = resource
        assets.update((asset.key, asset) for asset in leaf.scene.assets)
        updates.extend(update for update in leaf.session_updates if not isinstance(update, ActivePagers))
        events.extend(leaf.report.events)
    for asset in document.assets:
        assets[asset.key] = scene.Asset(asset.key, asset.name, asset.media_type)
        resources[f"asset:{asset.key}"] = asset
    updates.extend(update for update in broker.updates if not isinstance(update, ActivePagers))
    retained.update(update.key for update in updates if isinstance(update, CursorUpdate))
    updates.append(ActivePagers(frozenset(retained)))
    updates.append(RegionLayoutUpdate(RegionLayoutState(solver.fingerprint, tuple(allocations))))
    pager_keys = [pager.key for pager in (*broker.pagers, *(pager for leaf in leaves for pager in leaf.scene.pagers))]
    if len(set(pager_keys)) != len(pager_keys):
        message = "duplicate visible pager key across regions"
        raise LayoutInvariantError(message)
    planned = scene.Scene(
        scene.Codec.protocol,
        request.target.id,
        request.target.version,
        scene.ComponentsV2(tuple(children)),
        tuple(assets.values()),
        (*broker.pagers, *(pager for leaf in leaves for pager in leaf.scene.pagers)),
    )
    events.extend(
        PlanEvent("pagination.region", report.key, f"Region uses {report.pages} pages", after={"pages": report.pages})
        for report in reports
        if report.pages > 1
    )
    return PlanResult(
        planned,
        bindings,
        PlanReport(
            tuple(events), stable_fingerprint((document,)), scene.Codec.fingerprint(planned), regions=tuple(reports)
        ),
        form_bindings=forms,
        resources=resources,
        session_updates=tuple(updates),
        metrics=PlanMetrics(
            states_explored=solver.evaluations, search_fallback=any(leaf.metrics.search_fallback for leaf in leaves)
        ),
        cost=selected.cost + solver.document_cost,
        degradation=selected.loss,
    )

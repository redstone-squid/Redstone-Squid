"""Allocate persistent and pageable regions using bottom-up resource frontiers.

Each candidate carries an envelope that bounds every one of its pages. Parents
combine envelopes, so turning a page never borrows resources from a sibling.
Only the selected candidate is materialized into live handlers and cursor writes.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from itertools import product
from typing import Any
from urllib.parse import quote

from squid_ui import scene
from squid_ui.document import Document
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError, LayoutSearchExhaustedError, UnsolvableLayoutError
from squid_ui.pages import PagePosition
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.planning.identity import stable_fingerprint
from squid_ui.planning.navigation import PagerNav
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanEvent, PlanMetrics, PlanReport, PlanResult, RegionReport
from squid_ui.presentation import ActivePagers, CursorUpdate
from squid_ui.semantic import FallbackContent, Group, KeepWithNext, Region, Stack, Unbreakable
from squid_ui.text import Text


@dataclass(frozen=True, slots=True)
class _Layout:
    cost: ResourceCost
    loss: DegradationProfile = DegradationProfile()
    turns: int = 0
    parts: tuple[_Layout, ...] = ()
    leaf: PlanResult[Any] | None = None
    region: Region[Any, Any] | None = None
    key: str = ""
    pages: tuple[tuple[_Layout, ...], ...] = ()
    anchors: tuple[tuple[str, ...], ...] = ()
    header: _Layout | None = None
    footer: _Layout | None = None
    navigation: tuple[PlanResult[Any], ...] = ()


def _envelope(left: ResourceCost, right: ResourceCost) -> ResourceCost:
    return ResourceCost({axis: max(left.get(axis), right.get(axis)) for axis in left.axes + right.axes})


def _dominates(left: _Layout, right: _Layout) -> bool:
    return (
        left.loss <= right.loss
        and left.turns <= right.turns
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
    while isinstance(node, Unbreakable | KeepWithNext):
        node = node.node
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


class _Solver:
    def __init__(self, request: PlanRequest[Any, Any, Any]) -> None:
        self.request = request
        self.target = request.target.reserve(request.reservation)
        self.remaining = request.search_budget**2
        self.measurements = 0
        self.evaluations = 0
        self.compiled: dict[int, tuple[Any, tuple[_Layout, ...]]] = {}

    def spend(self) -> None:
        if self.remaining <= 0:
            msg = "region allocation exhausted its search budget before completing the frontier"
            raise LayoutSearchExhaustedError(msg)
        self.remaining -= 1
        self.evaluations += 1

    def compile(self, nodes: tuple[Any, ...], *, target: Any = None) -> PlanResult[Any] | None:
        self.spend()
        if self.measurements >= self.request.search_budget:
            message = "region allocation exhausted its measurement budget"
            raise LayoutSearchExhaustedError(message)
        self.measurements += 1
        try:
            return self.target.dialect.planner.plan(
                Document(nodes),
                replace(self.request, target=self.target if target is None else target, reservation=EMPTY_COST),
                cache=None,
                memo=None,
            )
        except UnsolvableLayoutError:
            return None

    def sequence(self, nodes: Sequence[Any], path: str) -> tuple[_Layout, ...]:
        frontier = (_Layout(EMPTY_COST),)
        keys: set[str] = set()
        for index, node in enumerate(nodes):
            key = _key(node)
            if key:
                if key in keys:
                    msg = f"{path}: duplicate sibling key {key!r}"
                    raise LayoutInvariantError(msg)
                keys.add(key)
            children = self.node(node, f"{path}/{quote(str(key), safe='')}" if key else f"{path}/@{index}")
            combined: list[_Layout] = []
            for left, right in product(frontier, children):
                self.spend()
                cost = left.cost + right.cost
                if cost.within(self.target.capacities):
                    combined.append(
                        _Layout(cost, left.loss.merged(right.loss), left.turns + right.turns, (*left.parts, right))
                    )
            frontier = _frontier(combined)
        return frontier

    def node(self, node: Any, path: str) -> tuple[_Layout, ...]:
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
                    )
                    for item in self.node(alternate, path)
                )
            return _frontier(choices)
        if isinstance(node, Unbreakable | KeepWithNext):
            return self.node(node.node, path)
        if contains_regions((node,)):
            msg = f"{path}: put nested regions in a region, group, or stack"
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
        while pending:
            candidate_target = pending.pop()
            identity = tuple(candidate_target.capacities.items())
            if identity in visited:
                continue
            visited.add(identity)
            planned = self.compile((node,), target=candidate_target)
            if planned is None:
                continue
            if planned.scene.pagers:
                message = f"{path}: replace legacy local pagination with a nested region"
                raise LayoutInvariantError(message)
            results.append(_Layout(planned.cost, loss=planned.degradation, leaf=planned))
            for axis, capacity in candidate_target.capacities.items():
                spent = planned.cost.get(axis)
                if spent:
                    pending.append(candidate_target.reserve(ResourceCost({axis: capacity - spent + 1})))
        frontier = _frontier(results)
        self.compiled[id(node)] = (node, frontier)
        return frontier

    def region(self, region: Region[Any, Any], path: str) -> tuple[_Layout, ...]:
        children = list(region.children)
        # A keep-with-next chain forms one legal break item. Unbreakable groups
        # already arrive as one child, even when their lowered representation expands.
        groups: list[tuple[Any, ...]] = []
        current: list[Any] = []
        anchors: list[tuple[str, ...]] = []
        seen: set[str] = set()
        for node in children:
            identity = node.node if isinstance(node, Unbreakable | KeepWithNext) else node
            key = getattr(identity, "key", None)
            if key:
                if key in seen:
                    msg = f"{path}: duplicate sibling key {key!r}"
                    raise LayoutInvariantError(msg)
                seen.add(key)
            current.append(node)
            if not isinstance(node, KeepWithNext):
                groups.append(tuple(current))
                anchors.append(tuple(key for item in current if (key := _key(item))))
                current = []
        if current:
            if groups:
                groups[-1] = (*groups[-1], *current)
                anchors[-1] = (*anchors[-1], *(key for item in current if (key := _key(item))))
            else:
                groups.append(tuple(current))
                anchors.append(tuple(key for item in current if (key := _key(item))))
        if not groups:
            groups = [()]
            anchors = [()]
        options = [self.sequence(group, path) for group in groups]
        headers = self.sequence(region.header, f"{path}/@header")
        footers = self.sequence(region.footer, f"{path}/@footer")
        results: list[_Layout] = []
        chrome = replace(
            self.request.chrome,
            previous=Text.join(" — ", (self.request.chrome.previous, region.title)),
            next=Text.join(" — ", (self.request.chrome.next, region.title)),
        )
        nav = None if self.request.nav is None else PagerNav(self.request.nav, self.request.presentation)
        broker = CursorCoordinator(self.request.presentation, chrome, nav)
        for extent in range(1, len(groups) + 1):
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
                        for end in range(start + 1, len(groups) - (extent - page_index - 1) + 1):
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
                                        )
                                    )
                            segments = _frontier(combined)
                            for prefix, segment in product(prefixes, segments):
                                self.spend()
                                cost = header.cost + segment.cost + navs[page_index].cost + footer.cost
                                if not cost.within(self.target.capacities):
                                    continue
                                candidate = _Layout(
                                    _envelope(prefix.cost, cost),
                                    prefix.loss.merged(segment.loss),
                                    prefix.turns + segment.turns,
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
                    )
                    for item in states.get(len(groups), ())
                )
            results = list(_frontier(results))
        return tuple(results)


def plan_regions(document: Document[Any, Any], request: PlanRequest[Any, Any, Any]) -> PlanResult[Any]:
    if request.target.body_type is not scene.ComponentsV2:
        msg = "joint regions currently require a Components V2 target"
        raise LayoutInvariantError(msg)
    solver = _Solver(request)
    candidates = solver.sequence(document.children, "$")
    if not candidates:
        msg = "no region allocation fits the target"
        raise UnsolvableLayoutError(msg, context={"reason": "region_allocation"})
    selected = min(candidates, key=lambda item: (item.loss, item.turns, tuple(item.cost.values.items())))
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
    retained: set[str] = set()

    def draw(item: _Layout, *, visible: bool = True) -> None:
        if item.leaf is not None:
            if visible:
                leaves.append(item.leaf)
            return
        if item.region is None:
            for part in item.parts:
                draw(part, visible=visible)
            return
        extent = len(item.pages)
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
            draw(item.header, visible=visible)
        for index, page in enumerate(item.pages):
            for part in page:
                draw(part, visible=visible and index == grant.position.page)
        if visible:
            leaves.append(item.navigation[grant.position.page])
        if item.footer is not None:
            draw(item.footer, visible=visible)

    draw(selected)
    bindings: dict[str, Any] = {}
    forms: dict[str, Any] = {}
    resources: dict[str, Any] = {}
    children: list[Any] = []
    updates: list[Any] = []
    events: list[PlanEvent] = []
    assets: dict[str, Any] = {}
    for leaf in leaves:
        for key in leaf.bindings:
            if key in bindings:
                msg = f"duplicate visible action key {key!r}"
                raise LayoutInvariantError(msg)
        bindings.update(leaf.bindings)
        forms.update(leaf.form_bindings)
        resources.update(leaf.resources)
        children.extend(leaf.scene.components_v2.children)
        assets.update((asset.key, asset) for asset in leaf.scene.assets)
        updates.extend(update for update in leaf.session_updates if not isinstance(update, ActivePagers))
        events.extend(leaf.report.events)
    for asset in document.assets:
        assets[asset.key] = scene.Asset(asset.key, asset.name, asset.media_type)
        resources[f"asset:{asset.key}"] = asset
    updates.extend(update for update in broker.updates if not isinstance(update, ActivePagers))
    retained.update(update.key for update in updates if isinstance(update, CursorUpdate))
    updates.append(ActivePagers(frozenset(retained)))
    planned = scene.Scene(
        scene.Codec.protocol,
        request.target.id,
        request.target.version,
        scene.ComponentsV2(tuple(children)),
        tuple(assets.values()),
        broker.pagers,
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
        metrics=PlanMetrics(states_explored=solver.evaluations),
        cost=selected.cost,
        degradation=selected.loss,
    )

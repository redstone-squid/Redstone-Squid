"""Plan logical documents into immutable target-resolved scenes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from squid_layouts.actions import ActionBinding
from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.document import Document, DocumentLike, as_document
from squid_layouts.errors import LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.planning.adaptation import SemanticLowering, lower_semantics, nominate_strategies
from squid_layouts.planning.cache import CachedPlan, PlanCache
from squid_layouts.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_layouts.planning.degradation import DegradationProfile
from squid_layouts.planning.identity import stable_fingerprint, stable_value
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.navigation import PlannedNav, materialized_navigation_state
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET, CostVector, StrategyAssignment, iter_assignments
from squid_layouts.planning.solve import (
    Realized,
    RPanel,
    RSection,
    RText,
    SolvedLayout,
    _resolve_variants,
    solve,
)
from squid_layouts.planning.target import ResourceCost, TargetProfile
from squid_layouts.primitives.constraints import Never, Paginate
from squid_layouts.primitives.nodes import (
    ActionGroup,
    Break,
    Budget,
    Button,
    Embed,
    Extension,
    Footer,
    Gallery,
    LinkButton,
    MediaCollection,
    Node,
    Panel,
    RawItem,
    RoutedButton,
    RoutedSelect,
    Row,
    Section,
    SelectMenu,
    Sep,
    Thumbnail,
    Variant,
    Variants,
)
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.scene.codec import SceneCodec
from squid_layouts.scene.model import (
    PlanEvent,
    PlanMetrics,
    PlanReport,
    PlanResult,
    PlanSeverity,
    SceneAsset,
    SceneButton,
    SceneDocument,
    SceneExtension,
    SceneGallery,
    SceneGalleryItem,
    SceneLink,
    SceneNode,
    SceneOption,
    ScenePanel,
    SceneRoutedButton,
    SceneRoutedSelect,
    SceneRow,
    SceneSection,
    SceneSelect,
    SceneSeparator,
    SceneText,
    SceneThumbnail,
)
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization

EMPTY_RESERVATION = ResourceCost()


@dataclass(slots=True)
class _Converter:
    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    resources: dict[str, object] = field(default_factory=dict)

    def action(self, node: Button | SelectMenu) -> str:
        key = node.key
        if key in self.bindings:
            message = f"duplicate action key {key!r}"
            raise LayoutInvariantError(message)
        handler = node.on_click if isinstance(node, Button) else node.on_select
        routes = node.routes if isinstance(node, SelectMenu) else {}
        for route_key, binding in routes.items():
            if route_key in self.bindings:
                message = f"duplicate action key {route_key!r}"
                raise LayoutInvariantError(message)
            self.bindings[route_key] = binding
        self.bindings[key] = ActionBinding(key=key, handler=handler, policy=node.policy, routes=routes)
        return key

    def accessory(self, node: Thumbnail | LinkButton | Button | RoutedButton | RawItem, path: str) -> SceneNode:
        match node:
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case LinkButton(label=label, url=url):
                return SceneLink(label, url)
            case RoutedButton(label=label, route_id=route_id):
                # No binding: the router owns dispatch, so the scene is complete without one.
                return SceneRoutedButton(
                    label=label,
                    route_id=route_id,
                    style=node.style,
                    emoji=node.emoji,
                    disabled=node.disabled,
                )
            case Button():
                return SceneButton(
                    label=node.label,
                    action=self.action(node),
                    style=node.style,
                    emoji=node.emoji,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case RawItem(factory=factory, kind=kind, version=version, payload=payload):
                resource = f"native:{path}"
                self.resources[resource] = factory()
                return SceneExtension(kind, version, {**payload, "resource": resource})

    def node(self, node: Realized, path: str) -> SceneNode:
        match node:
            case RText(content=content):
                return SceneText(content)
            case RPanel(children=children, accent=accent):
                return ScenePanel(
                    tuple(self.node(child, f"{path}.{index}") for index, child in enumerate(children)), accent
                )
            case RSection(texts=texts, accessory=accessory):
                return SceneSection(
                    tuple(SceneText(text.content) for text in texts),
                    self.accessory(accessory, f"{path}.accessory"),
                )
            case Sep(large=large, visible=visible):
                return SceneSeparator(large, visible)
            case Row(items=items):
                converted = tuple(self.accessory(item, f"{path}.{index}") for index, item in enumerate(items))
                if not all(
                    isinstance(item, SceneLink | SceneButton | SceneRoutedButton | SceneExtension) for item in converted
                ):
                    message = f"row {path} contains an unsupported item"
                    raise LayoutInvariantError(message)
                return SceneRow(converted)
            case SelectMenu(options=options):
                return SceneSelect(
                    options=tuple(
                        SceneOption(option.label, option.value, option.description, option.default)
                        for option in options
                    ),
                    action=self.action(node),
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                    policy=node.policy,
                )
            case RoutedSelect(options=options):
                return SceneRoutedSelect(
                    options=tuple(
                        SceneOption(option.label, option.value, option.description, option.default)
                        for option in options
                    ),
                    route_id=node.route_id,
                    placeholder=node.placeholder,
                    min_values=node.min_values,
                    max_values=node.max_values,
                    disabled=node.disabled,
                )
            case Thumbnail(url=url, description=description):
                return SceneThumbnail(url, description)
            case Gallery(urls=urls):
                return SceneGallery(tuple(SceneGalleryItem(url) for url in urls))
            case RawItem():
                return self.accessory(node, path)
            case LinkButton() | RoutedButton():
                return self.accessory(node, path)

    def children(self, children: Sequence[Realized]) -> tuple[SceneNode, ...]:
        return tuple(self.node(child, str(index)) for index, child in enumerate(children))


def _lower_children(
    nodes: Sequence[Node],
    target: TargetProfile,
    limits: V2Limits,
) -> tuple[Node, ...]:
    lowered: list[Node] = []
    for node in nodes:
        match node:
            case ActionGroup(items=items):
                lowered.extend(
                    Row(tuple(items[start : start + limits.row_buttons]))
                    for start in range(0, len(items), limits.row_buttons)
                )
            case MediaCollection(urls=urls):
                lowered.extend(
                    Gallery(tuple(urls[start : start + limits.gallery_items]))
                    for start in range(0, len(urls), limits.gallery_items)
                )
            case Panel(children=children, accent=accent):
                lowered.append(Panel(_lower_children(children, target, limits), accent))
            case Budget(children=children) | Break(children=children):
                lowered.append(replace(node, children=_lower_children(children, target, limits)))
            case Extension(kind=kind, version=version, payload=payload, fallback=fallback):
                adapter = target.extensions.get(kind)
                if adapter is None:
                    lowered.extend(_lower_children((fallback,), target, limits))
                    continue
                prepared = adapter.prepare(payload)
                component_cost = prepared.cost.get("components")
                text_cost = prepared.cost.get("display_text")
                if component_cost < 1 or text_cost < 0:
                    message = f"extension adapter {kind!r} returned an invalid resource cost"
                    raise LayoutInvariantError(message)
                resource = prepared.resource
                lowered.append(
                    RawItem(
                        factory=lambda resource=resource: resource,
                        text_cost=text_cost,
                        component_cost=component_cost,
                        kind=kind,
                        version=version,
                        payload=prepared.scene_payload,
                    )
                )
            case Variants(variants=variants, priority=priority):
                supported = [variant for variant in variants if variant.requires <= target.capabilities]
                if not supported:
                    message = "Variants has no variant supported by the selected target"
                    raise LayoutInvariantError(message)
                # `requires` is cleared rather than carried: capability selection happens here
                # and exactly once, leaving the solver a pure budget ladder.
                lowered.append(
                    Variants(
                        tuple(Variant(_lower_children(variant.nodes, target, limits)) for variant in supported),
                        priority,
                    )
                )
            case _:
                lowered.append(node)
    return tuple(lowered)


def _validate(nodes: Sequence[Node], limits: V2Limits) -> None:
    pager_keys: set[str] = set()

    def fail(path: str, detail: str) -> None:
        message = f"{path}: {detail}"
        raise LayoutInvariantError(message)

    def walk(node: Node, path: str) -> None:
        if isinstance(node, Embed):
            fail(path, "Embed must be expanded by a component mount before planning")
        overflow = getattr(node, "overflow", None)
        if isinstance(overflow, Paginate):
            if overflow.key is None:
                fail(path, "Paginate requires an explicit key")
            if overflow.key in pager_keys:
                fail(path, f"duplicate pager key {overflow.key!r}")
            pager_keys.add(overflow.key)
        match node:
            case Button(label=label):
                if len(label) > limits.button_label:
                    fail(path, f"button label exceeds {limits.button_label}")
            case Row(items=items):
                if len(items) > limits.row_buttons:
                    fail(path, f"row has {len(items)} controls; maximum is {limits.row_buttons}")
                for index, item in enumerate(items):
                    if isinstance(item, Button):
                        walk(item, f"{path}.{index}")
            case SelectMenu(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum) | (
                RoutedSelect(options=options, placeholder=placeholder, min_values=minimum, max_values=maximum)
            ):
                if not options:
                    fail(path, "select needs at least one option")
                if len(options) > limits.select_options:
                    remedy = (
                        "split the routed picker into separate routes"
                        if isinstance(node, RoutedSelect)
                        else "use an option-paging semantic node"
                    )
                    fail(path, f"select has {len(options)} options; {remedy}")
                if placeholder is not None and len(placeholder) > limits.select_placeholder:
                    fail(path, f"select placeholder exceeds {limits.select_placeholder}")
                if minimum < 0 or maximum < minimum or maximum > max(1, len(options)):
                    fail(path, "select value bounds are invalid")
                for index, option in enumerate(options):
                    if len(option.label) > limits.option_label:
                        fail(f"{path}.option.{index}", f"label exceeds {limits.option_label}")
                    if len(option.value) > limits.option_value:
                        fail(f"{path}.option.{index}", f"value exceeds {limits.option_value}")
                    if option.description is not None and len(option.description) > limits.option_description:
                        fail(f"{path}.option.{index}", f"description exceeds {limits.option_description}")
            case Gallery(urls=urls):
                if len(urls) > limits.gallery_items:
                    fail(path, f"gallery has {len(urls)} items; use MediaCollection")
            case Section(texts=texts):
                if len(texts) > limits.section_texts:
                    fail(path, f"section has {len(texts)} text slots; maximum is {limits.section_texts}")
                for index, text in enumerate(texts):
                    if isinstance(text.overflow, Paginate):
                        fail(
                            f"{path}.text.{index}",
                            "Paginate cannot be nested in a Section; place it beside the Section",
                        )
                    walk(text, f"{path}.text.{index}")
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                for index, child in enumerate(children):
                    walk(child, f"{path}.{index}")
            case Variants(variants=variants):
                # Every rung is checked, not just the one the solver will open on, so a
                # document is rejected for a bad rung it might never reach. That also means
                # two rungs cannot share a Paginate key — as under the previous Fold, whose
                # primary and fallback were both walked.
                for index, variant in enumerate(variants):
                    for child_index, child in enumerate(variant.nodes):
                        walk(child, f"{path}.variant.{index}.{child_index}")
            case _:
                return

    for index, node in enumerate(nodes):
        walk(node, f"$.{index}")


@dataclass(frozen=True, slots=True)
class _StrategyAttempt:
    assignment: StrategyAssignment
    semantic: SemanticLowering
    lowered: tuple[Node, ...]
    solved: SolvedLayout
    broker: CursorCoordinator


def _attempt_strategy(
    assignment: StrategyAssignment,
    *,
    document: Document,
    target: TargetProfile,
    limits: V2Limits,
    chrome: Chrome,
    localization: Localization,
    presentation: PresentationSession,
    reservation: ResourceCost,
    positions: Mapping[str, Position] | None,
    nav: PlannedNav | None,
    search_budget: int,
) -> _StrategyAttempt:
    broker = CursorCoordinator(presentation, chrome, nav, positions)
    semantic = lower_semantics(
        document.children,
        limits=limits,
        chrome=chrome,
        localization=localization,
        session=presentation,
        pages=broker,
        capabilities=target.capabilities,
        strategies=dict(assignment.strategies),
    )
    lowered = _lower_children(semantic.nodes, target, limits)
    _validate(lowered, limits)
    solved = solve(
        lowered,
        limits=limits,
        chrome=chrome,
        strict=False,
        reserved_text=reservation.get("display_text"),
        nav=nav,
        search_budget=search_budget,
    )
    return _StrategyAttempt(assignment, semantic, lowered, solved, broker)


def _search_strategies(
    *,
    document: Document,
    target: TargetProfile,
    limits: V2Limits,
    chrome: Chrome,
    localization: Localization,
    presentation: PresentationSession,
    reservation: ResourceCost,
    positions: Mapping[str, Position] | None,
    nav: PlannedNav | None,
    search_budget: int,
) -> _StrategyAttempt:
    axes = nominate_strategies(document.children, limits=limits, session=presentation)
    assignments = iter(iter_assignments(axes))
    current = next(assignments)
    first: _StrategyAttempt | None = None
    degraded: _StrategyAttempt | None = None
    phase_one: list[_StrategyAttempt] = []
    selected: _StrategyAttempt | None = None
    fallback = False
    states_explored = 0

    # Lossless semantic representations always outrank author-granted degradation. Give
    # every semantic assignment its preferred structural rungs before spending another
    # state inside any one assignment's variant product.
    while True:
        attempted = _attempt_strategy(
            current,
            document=document,
            target=target,
            limits=limits,
            chrome=chrome,
            localization=localization,
            presentation=presentation,
            reservation=reservation,
            positions=positions,
            nav=nav,
            search_budget=1,
        )
        states_explored += attempted.solved.states_explored
        phase_one.append(attempted)
        if first is None:
            first = attempted
        fits = attempted.solved.components <= limits.total_components and not attempted.solved.failures
        if fits and (degraded is None or _degraded_key(attempted) < _degraded_key(degraded)):
            degraded = attempted

        following = next(assignments, None)
        if following is not None and states_explored >= search_budget:
            selected = degraded or first
            fallback = True
            break
        if fits and attempted.solved.degradation.lossless:
            selected = attempted
            break
        if following is None:
            break
        current = following

    # No preferred-rung assignment was lossless. Resume only assignments that actually
    # had unexplored variant states, retaining the best incumbent across the full product.
    if selected is None:
        for initial in phase_one:
            if not initial.solved.search_fallback:
                continue
            remaining = search_budget - states_explored
            if remaining < 1:
                fallback = True
                break
            attempted = _attempt_strategy(
                initial.assignment,
                document=document,
                target=target,
                limits=limits,
                chrome=chrome,
                localization=localization,
                presentation=presentation,
                reservation=reservation,
                positions=positions,
                nav=nav,
                search_budget=remaining,
            )
            states_explored += attempted.solved.states_explored
            fits = attempted.solved.components <= limits.total_components and not attempted.solved.failures
            if fits and (degraded is None or _degraded_key(attempted) < _degraded_key(degraded)):
                degraded = attempted
            if attempted.solved.search_fallback:
                fallback = True
                break
        selected = degraded or first

    assert selected is not None
    fallback_events: tuple[PlanEvent, ...] = ()
    if fallback:
        fallback_events = (
            PlanEvent(
                code="planner.search_fallback",
                path="$",
                message=(
                    f"Strategy search used its bounded fallback within {search_budget} attempts; "
                    "selected the best incumbent"
                ),
                severity=PlanSeverity.WARNING,
            ),
        )
    semantic = replace(
        selected.semantic,
        events=fallback_events + selected.semantic.events,
        states_explored=states_explored,
        search_fallback=fallback,
    )
    return replace(selected, semantic=semantic)


def _degraded_key(attempt: _StrategyAttempt) -> tuple[DegradationProfile, CostVector]:
    return attempt.solved.degradation, attempt.assignment.cost


def plan(
    rendered: DocumentLike,
    *,
    target: TargetProfile,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    positions: Mapping[str, Position] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationSession | None = None,
    cache: PlanCache | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> PlanResult:
    """Resolve a complete logical document for one target.

    Planning owns every fit and fallback decision. The resulting scene contains visual action
    references, while callbacks remain in the plan result for the mounted frontend.
    """
    if search_budget < 1:
        message = "planner search budget must be positive"
        raise ValueError(message)
    document = as_document(rendered)
    limits = target.limits if isinstance(target.limits, V2Limits) else LIMITS
    presentation = session if session is not None else PresentationSession()
    chrome = localize_chrome(chrome, localization)
    cache_key = _plan_cache_key(
        (document,),
        target=target,
        limits=limits,
        chrome=chrome,
        localization=localization,
        presentation=presentation,
        reservation=reservation,
        strict=strict,
        nav=nav,
        positions=positions,
        search_budget=search_budget,
    )
    cached = cache.get(cache_key) if cache is not None else None
    if cached is not None:
        broker = CursorCoordinator(presentation, chrome, nav, positions)
        semantic = lower_semantics(
            document.children,
            limits=limits,
            chrome=chrome,
            localization=localization,
            session=presentation,
            pages=broker,
            capabilities=target.capabilities,
            strategies=dict(cached.strategies),
        )
        lowered = _lower_children(semantic.nodes, target, limits)
        _validate(lowered, limits)
        selected_nodes = _resolve_variants(lowered, dict(cached.variant_positions))
        converter = _collect_cached_bindings(selected_nodes, cached.scene, nav, chrome)
        resources = {f"asset:{asset.key}": asset for asset in document.assets}
        return PlanResult(
            scene=cached.scene,
            bindings=converter.bindings,
            report=cached.report,
            resources=resources,
            metrics=PlanMetrics(
                states_explored=cached.states_explored,
                cache_hit=True,
                search_fallback=cached.search_fallback,
            ),
            session_updates=cached.session_updates,
        )

    attempt = _search_strategies(
        document=document,
        target=target,
        limits=limits,
        chrome=chrome,
        localization=localization,
        presentation=presentation,
        reservation=reservation,
        positions=positions,
        nav=nav,
        search_budget=search_budget,
    )
    broker = attempt.broker
    semantic = attempt.semantic
    lowered = attempt.lowered
    solved = attempt.solved
    root_events: tuple[PlanEvent, ...] = ()
    if solved.components > limits.total_components:
        local_pagers = [*broker.pagers, *solved.pagers]
        if local_pagers:
            keys = ", ".join(repr(pager.key) for pager in local_pagers)
            message = (
                f"{solved.components} components exceed target maximum {limits.total_components} after local "
                f"pagination ({keys}). Local and root pagination are never simultaneous; fold component groups, "
                "split the document, or move the long local collection onto its own screen."
            )
            raise UnsolvableLayoutError(message)
        if document.key is None or nav is None:
            remedy = (
                "give Document an explicit key and plan with navigation controls to allow root pagination"
                if document.key is None
                else "plan with navigation controls or split the static document"
            )
            message = f"{solved.components} components exceed target maximum {limits.total_components}; {remedy}"
            raise UnsolvableLayoutError(message)
        solved, root_pages = _root_paginate(
            lowered,
            key=document.key,
            target_limits=limits,
            chrome=chrome,
            reserved_text=reservation.get("display_text"),
            nav=nav,
            broker=broker,
        )
        root_events = (
            PlanEvent(
                code="pagination.root",
                path="$",
                message=f"Document {document.key!r} uses {root_pages} lossless root pages",
                severity=PlanSeverity.ADAPTATION,
                after={"pages": root_pages},
            ),
        )
    _reconcile_pagers(solved, broker)
    if strict and solved.notes:
        raise LayoutDegradedError("; ".join(note.message for note in solved.notes))
    hard_failures = solved.failures
    if hard_failures:
        message = "; ".join(note.message for note in hard_failures)
        raise UnsolvableLayoutError(message)
    converter = _Converter()
    scene = SceneDocument(
        protocol=SceneCodec.protocol,
        target=target.id,
        target_version=target.version,
        children=converter.children(solved.children),
        assets=tuple(SceneAsset(asset.key, asset.name, asset.media_type) for asset in document.assets),
        pagers=broker.pagers,
    )
    fingerprint = SceneCodec.fingerprint(scene)
    report = PlanReport(
        events=semantic.events
        + root_events
        + tuple(
            PlanEvent(
                code=f"layout.{note.code}",
                path="$",
                message=note.message,
                severity=PlanSeverity.DEGRADATION,
            )
            for note in solved.notes
        ),
        logical_fingerprint=stable_fingerprint((document,)),
        scene_fingerprint=fingerprint,
    )
    resources = dict(converter.resources)
    resources.update({f"asset:{asset.key}": asset for asset in document.assets})
    updates = semantic.updates + broker.updates
    result = PlanResult(
        scene=scene,
        bindings=converter.bindings,
        report=report,
        resources=resources,
        metrics=PlanMetrics(
            states_explored=semantic.states_explored,
            search_fallback=semantic.search_fallback,
        ),
        session_updates=updates,
    )
    if cache is not None and _cacheable(lowered):
        cache.put(
            cache_key,
            CachedPlan(
                scene,
                report,
                updates,
                attempt.assignment.strategies,
                semantic.states_explored,
                semantic.search_fallback,
                attempt.solved.variant_positions,
            ),
        )
    return result


def _reconcile_pagers(solved: SolvedLayout, broker: CursorCoordinator) -> None:
    """Move each solved pager to the page its reader belongs on.

    The solver has to render *some* page to measure one, and it picks before anyone knows
    how many there are — the count is an output of fitting. This is the first moment a
    stored cursor can be reconciled against it, and because the page is a projection it
    costs a slot rewrite rather than a second solve. The mount used to do this by drawing
    twice.
    """
    positions: dict[str, Position] = {}
    for pager in solved.pagers:
        request = MaterializedCursorRequest(
            key=pager.key,
            extent=pager.pages,
            fingerprint=content_fingerprint(pager.fragments),
            initial="end" if pager.initial else "start",
        )
        grant = broker.grant(request)
        broker.record(request, grant.position)
        positions[pager.key] = grant.position
    solved.reposition(positions)


def _root_paginate(
    nodes: Sequence[Node],
    *,
    key: str,
    target_limits: V2Limits,
    chrome: Chrome,
    reserved_text: int,
    nav: PlannedNav,
    broker: CursorCoordinator,
) -> tuple[SolvedLayout, int]:
    maximum_pages = max(1, len(nodes))

    def solve_page(children: Sequence[Node], index: int, pages: int) -> SolvedLayout:
        chrome_nodes: tuple[Node, ...] = (
            Footer(chrome.page_footer(index + 1, pages), overflow=Never()),
            *nav(materialized_navigation_state(key, Position(offset=index), pages, chrome)),
        )
        return solve(
            (*children, *chrome_nodes),
            limits=target_limits,
            chrome=chrome,
            strict=False,
            reserved_text=reserved_text,
            nav=nav,
        )

    # Greedy: grow a page until it stops fitting, then cut. Every probe measures a
    # different prefix, so there is nothing to memoize — the cost is one solve per node,
    # paid only by a document that already blew the component budget.
    pages: list[tuple[Node, ...]] = []
    current: tuple[Node, ...] = ()
    for node in nodes:
        candidate = (*current, node)
        probe = solve_page(candidate, 0, maximum_pages)
        if current and (probe.components > target_limits.total_components or probe.overflowed):
            pages.append(current)
            current = (node,)
            probe = solve_page(current, 0, maximum_pages)
        else:
            current = candidate
        if probe.components > target_limits.total_components:
            message = (
                f"root page {len(pages) + 1} cannot fit node {type(node).__name__}; "
                "give that node a structural fallback or move it to another screen"
            )
            raise UnsolvableLayoutError(message)
    if current:
        pages.append(current)

    request = MaterializedCursorRequest(key=key, extent=len(pages), fingerprint=stable_fingerprint(nodes))
    grant = broker.grant(request)
    broker.record(request, grant.position)
    index = grant.position.offset
    return solve_page(pages[index], index, grant.extent), grant.extent


def _plan_cache_key(
    nodes: Sequence[object],
    *,
    target: TargetProfile,
    limits: V2Limits,
    chrome: Chrome,
    localization: Localization,
    presentation: PresentationSession,
    reservation: ResourceCost,
    strict: bool,
    nav: PlannedNav | None,
    positions: Mapping[str, Position] | None,
    search_budget: int,
) -> str:
    relevant = {
        "document": stable_value(nodes),
        "target": (target.id, target.version),
        "limits": stable_value(limits),
        "presentation": stable_value(presentation),
        "chrome": (
            chrome.previous,
            chrome.next,
            chrome.back,
            chrome.home,
            chrome.close,
            chrome.page_footer(1, 2),
            chrome.and_n_more(2),
        ),
        "locale": localization.locale,
        "reservation": stable_value(reservation),
        "strict": strict,
        "positions": stable_value(positions),
        "search_budget": search_budget,
        "nav": (
            None
            if nav is None
            else (
                getattr(nav, "__module__", ""),
                getattr(nav, "__qualname__", type(nav).__qualname__),
                getattr(nav, "version", 0),
            )
        ),
    }
    return stable_fingerprint((relevant,))


def _cacheable(nodes: Sequence[Node]) -> bool:
    def check(node: Node) -> bool:
        if isinstance(node, Extension | RawItem):
            return False
        if isinstance(node, Variants):
            return all(check(child) for variant in node.variants for child in variant.nodes)
        if isinstance(node, Panel | Budget | Break):
            return all(check(child) for child in node.children)
        return True

    return all(check(node) for node in nodes)


def _collect_bindings(nodes: Sequence[Node]) -> _Converter:
    converter = _Converter()

    def collect(node: Node) -> None:
        match node:
            case Button() | SelectMenu():
                converter.action(node)
            case Row(items=items):
                for item in items:
                    if isinstance(item, Button):
                        converter.action(item)
            case Section(accessory=accessory):
                if isinstance(accessory, Button):
                    converter.action(accessory)
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                for child in children:
                    collect(child)
            case _:
                return

    for node in nodes:
        collect(node)
    return converter


def _collect_cached_bindings(
    nodes: Sequence[Node],
    scene: SceneDocument,
    nav: PlannedNav | None,
    chrome: Chrome,
) -> _Converter:
    converter = _collect_bindings(nodes)
    if nav is None:
        return converter
    for pager in scene.pagers:
        generated = _collect_bindings(
            nav(materialized_navigation_state(pager.key, Position(offset=pager.page), pager.pages, chrome))
        )
        for key, binding in generated.bindings.items():
            converter.bindings.setdefault(key, binding)
    return converter

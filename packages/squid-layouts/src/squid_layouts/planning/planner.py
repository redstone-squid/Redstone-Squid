"""Plan logical documents into immutable target-resolved scenes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC
from heapq import heappop, heappush
from itertools import count

from squid_layouts.actions import ActionBinding
from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.document import Document, DocumentLike, as_document
from squid_layouts.errors import LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
from squid_layouts.forms import FormBinding
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.adaptation import (
    FallbackAxis,
    SemanticDecisions,
    SemanticLowering,
    lower_semantics,
    nominate_decisions,
)
from squid_layouts.planning.cache import CachedPlan, PlanCache
from squid_layouts.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_layouts.planning.degradation import DegradationEffect, DegradationProfile
from squid_layouts.planning.frontier import (
    VariantPath,
    canonical_positions,
    guided_step,
    resolve_variants,
    steppable,
    variant_notes,
    variant_profile,
    variant_state_bound,
)
from squid_layouts.planning.identity import stable_fingerprint, stable_value
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.measure import (
    MeasuredLayout,
    Realized,
    RPanel,
    RSection,
    RText,
    RTime,
    SolveNote,
    SolveNoteCode,
    measure,
)
from squid_layouts.planning.navigation import PlannedNav, materialized_navigation_state
from squid_layouts.planning.search import (
    DEFAULT_SEARCH_BUDGET,
    CostVector,
    StrategyAxis,
    assignment_cost,
    ranked_candidates,
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
    File,
    Footer,
    FormButton,
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
    SceneFile,
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
    SceneTime,
)
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization

EMPTY_RESERVATION = ResourceCost()


def _merge_assets(*groups: Sequence[Asset]) -> tuple[Asset, ...]:
    merged: dict[str, Asset] = {}
    for asset in (asset for group in groups for asset in group):
        existing = merged.get(asset.key)
        if existing is not None and existing != asset:
            message = f"asset key {asset.key!r} identifies two different assets"
            raise LayoutInvariantError(message)
        merged.setdefault(asset.key, asset)
    return tuple(merged.values())


@dataclass(slots=True)
class _Converter:
    bindings: dict[str, ActionBinding] = field(default_factory=dict)
    form_bindings: dict[str, FormBinding] = field(default_factory=dict)
    resources: dict[str, object] = field(default_factory=dict)

    def action(self, node: Button | SelectMenu) -> str:
        key = node.key
        if isinstance(node, FormButton) and node.form is not None:
            # Recorded beside the binding, not in place of it: the button presents the form
            # and the binding submits it, and both answer to the same key.
            self.form_bindings[key] = node.form
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
            case RTime(instant=instant, style=style, prefix=prefix):
                return SceneTime(instant.astimezone(UTC).isoformat(), style, prefix)
            case File(asset_key=asset_key, name=name, media_type=media_type):
                return SceneFile(asset_key, name, media_type)
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
                # and exactly once, leaving the search a pure budget ladder whose rung
                # numbering is stable for the rest of the plan.
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
                # Every rung is checked, not just the one the search will open on, so a
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
class _State:
    """One point in the planner's decision space, canonical by construction.

    Fallback branches and strategies are named by semantic path, so they survive a change
    anywhere else in the document. Primitive ladder positions are named by their path through
    the *selected* rungs, which is only meaningful against one lowering — so any semantic
    change discards them rather than reinterpreting them against a different tree.
    """

    fallbacks: tuple[tuple[str, int], ...] = ()
    strategies: tuple[tuple[str, str], ...] = ()
    variants: tuple[tuple[VariantPath, int], ...] = ()


def _ordered(positions: Mapping[VariantPath, int]) -> tuple[tuple[VariantPath, int], ...]:
    """Give a position set exactly one spelling, so equal states compare equal.

    Paths mix rung indices with container markers, so they are ordered as text rather than
    compared element by element.
    """
    return tuple(sorted(positions.items(), key=lambda item: [str(part) for part in item[0]]))


@dataclass(frozen=True, slots=True)
class _Candidate:
    """One fully evaluated state: what it decided, what it costs, and what it measured."""

    state: _State
    decisions: SemanticDecisions
    semantic: SemanticLowering
    lowered: tuple[Node, ...]
    """Target-lowered and validated, with primitive ladders still unresolved."""
    layout: MeasuredLayout
    broker: CursorCoordinator
    structural: DegradationProfile
    cost: CostVector

    @property
    def degradation(self) -> DegradationProfile:
        return self.layout.degradation

    @property
    def feasible(self) -> bool:
        return self.layout.components <= self.layout.limits.total_components and not self.layout.failures

    @property
    def rank(self) -> tuple[DegradationProfile, CostVector]:
        return self.degradation, self.cost


@dataclass(frozen=True, slots=True)
class _Expansion:
    """The decisions one step away, and whether reaching them cost the search its guarantee."""

    successors: tuple[tuple[DegradationProfile, CostVector, _State], ...]
    guided: bool


@dataclass(frozen=True, slots=True)
class _Search:
    """Everything one document's search needs that does not change between candidates."""

    document: Document
    target: TargetProfile
    limits: V2Limits
    chrome: Chrome
    localization: Localization
    palette: Palette
    presentation: PresentationSession
    reservation: ResourceCost
    positions: Mapping[str, Position] | None
    nav: PlannedNav | None

    def evaluate(self, state: _State) -> _Candidate:
        """Lower, validate, and measure exactly one candidate in its own coordinator.

        The coordinator is per candidate rather than per plan: a rejected state must not
        leave its pagers, assets, events, bindings, or staged session writes behind.
        """
        broker = CursorCoordinator(self.presentation, self.chrome, self.nav, self.positions)
        fallbacks = dict(state.fallbacks)
        decisions = nominate_decisions(
            self.document.children,
            limits=self.limits,
            session=self.presentation,
            fallbacks=fallbacks,
        )
        strategies = _canonical_strategies(decisions.strategies, dict(state.strategies))
        # Decisions the selected branches no longer expose are dropped rather than carried:
        # a state is identified by what it actually decided, so the frontier cannot hold two
        # spellings of the same candidate.
        reachable = {occurrence.path for occurrence in decisions.fallbacks}
        fallbacks = {path: rung for path, rung in fallbacks.items() if rung and path in reachable}
        semantic = lower_semantics(
            self.document.children,
            limits=self.limits,
            chrome=self.chrome,
            localization=self.localization,
            palette=self.palette,
            session=self.presentation,
            pages=broker,
            capabilities=self.target.capabilities,
            strategies=strategies,
            fallbacks=fallbacks,
        )
        lowered = _lower_children(semantic.nodes, self.target, self.limits)
        _validate(lowered, self.limits)
        variants = canonical_positions(lowered, dict(state.variants))
        steps = [*_fallback_notes(decisions.fallbacks, fallbacks), *variant_notes(lowered, variants)]
        layout = measure(
            resolve_variants(lowered, variants),
            limits=self.limits,
            chrome=self.chrome,
            reserved_text=self.reservation.get("display_text"),
            nav=self.nav,
        )
        structural = _fallback_profile(decisions.fallbacks, fallbacks).merged(variant_profile(lowered, variants))
        if steps:
            layout = replace(layout, notes=[*steps, *layout.notes], overflowed=True)
        layout = replace(layout, degradation=layout.degradation.merged(structural))
        return _Candidate(
            _State(
                tuple(sorted(fallbacks.items())),
                tuple(sorted(strategies.items())),
                _ordered(variants),
            ),
            decisions,
            semantic,
            lowered,
            layout,
            broker,
            structural,
            assignment_cost(decisions.strategies, strategies),
        )

    def successors(self, candidate: _Candidate, remaining: int) -> _Expansion:
        """Every decision one step away, priced by the loss it already commits to.

        The bound is exact for strategy and ladder steps. Opening a different fallback branch
        rebuilds the axis set, so its semantic cost is only known once it is evaluated; the
        zero vector is used until then, which delays a stop but never prunes a better answer.
        """
        state = candidate.state
        found: list[tuple[DegradationProfile, CostVector, _State]] = []
        strategies = dict(state.strategies)
        for axis in candidate.decisions.strategies:
            ranked = [item.strategy_id for _cost, item in ranked_candidates(axis)]
            following = ranked.index(strategies[axis.path]) + 1
            if following >= len(ranked):
                continue
            advanced = {**strategies, axis.path: ranked[following]}
            found.append(
                (
                    candidate.structural,
                    assignment_cost(candidate.decisions.strategies, advanced),
                    # A different representation renumbers the tree, so ladder positions
                    # measured against the old one mean nothing against the new one.
                    _State(state.fallbacks, tuple(sorted(advanced.items())), ()),
                )
            )
        fallbacks = dict(state.fallbacks)
        for occurrence in candidate.decisions.fallbacks:
            rung = fallbacks.get(occurrence.path, 0)
            if rung + 1 >= occurrence.branches:
                continue
            opened = {**fallbacks, occurrence.path: rung + 1}
            surviving = {
                path: strategy for path, strategy in strategies.items() if not path.startswith(f"{occurrence.path}.")
            }
            found.append(
                (
                    _fallback_profile(candidate.decisions.fallbacks, opened),
                    CostVector(),
                    _State(tuple(sorted(opened.items())), tuple(sorted(surviving.items())), ()),
                )
            )
        variants = dict(state.variants)
        fallback_loss = _fallback_profile(candidate.decisions.fallbacks, fallbacks)
        neighbours, guided = self._ladder_steps(candidate, variants, remaining)
        found.extend(
            (
                fallback_loss.merged(variant_profile(candidate.lowered, neighbour)),
                candidate.cost,
                _State(state.fallbacks, state.strategies, _ordered(neighbour)),
            )
            for neighbour in neighbours
        )
        return _Expansion(tuple(found), guided)

    def _ladder_steps(
        self, candidate: _Candidate, variants: dict[VariantPath, int], remaining: int
    ) -> tuple[list[dict[VariantPath, int]], bool]:
        """Which ladder assignments to consider next, exhaustively or under guidance.

        A product the remaining budget cannot exhaust is walked one deterministic step at a
        time instead: breadth and priority still choose the eligible ladders, and among them
        the step that frees the most components wins.
        """
        eligible = steppable(candidate.lowered, variants)
        if variant_state_bound(candidate.lowered, remaining) > remaining:
            step = guided_step(candidate.lowered, variants, self.limits)
            # Guidance discards the siblings it did not take, so the incumbent it reaches is
            # the best one *found*, not the best one that exists. That is a bounded fallback.
            return ([] if step is None else [step], len(eligible) > 1)
        return (
            [canonical_positions(candidate.lowered, {**variants, path: rung + 1}) for path, _ladder, rung in eligible],
            False,
        )


def _canonical_strategies(axes: Sequence[StrategyAxis], selected: Mapping[str, str]) -> dict[str, str]:
    """Keep a valid choice per reachable axis; a newly reachable one opens at its cheapest."""
    resolved: dict[str, str] = {}
    for axis in axes:
        chosen = selected.get(axis.path)
        if chosen is None or chosen not in {candidate.strategy_id for candidate in axis.candidates}:
            chosen = ranked_candidates(axis)[0][1].strategy_id
        resolved[axis.path] = chosen
    return resolved


def _fallback_profile(occurrences: Sequence[FallbackAxis], selected: Mapping[str, int]) -> DegradationProfile:
    """Price opened fallback branches as the author-granted loss they are."""
    profile = DegradationProfile()
    for occurrence in occurrences:
        rung = selected.get(occurrence.path, 0)
        if rung:
            profile = profile.with_effect(DegradationEffect(priority=0, path=occurrence.path, semantic_steps=rung))
    return profile


def _fallback_notes(occurrences: Sequence[FallbackAxis], selected: Mapping[str, int]) -> list[SolveNote]:
    return [
        SolveNote(
            SolveNoteCode.VARIANT_STEP,
            f"{occurrence.path} stepped to variant {step + 2} of {occurrence.branches} "
            "(priority 0) under layout pressure",
        )
        for occurrence in occurrences
        for step in range(selected.get(occurrence.path, 0))
    ]


def _search(search: _Search, *, search_budget: int) -> _Candidate:
    """Explore one conditional decision graph best-first and return the least degraded fit.

    Strategies, semantic fallbacks, and primitive ladders are all decisions in the same
    frontier, so a lossless representation is never traded for a structural fallback the
    author priced higher, and an alternative hidden behind an unopened branch costs nothing.
    """
    frontier: list[tuple[DegradationProfile, CostVector, int, _State]] = []
    serial = count()
    heappush(frontier, (DegradationProfile(), CostVector(), next(serial), _State()))
    seen: set[_State] = {_State()}
    best: _Candidate | None = None
    nearest: _Candidate | None = None
    states_explored = 0
    guided = False

    while frontier and states_explored < search_budget:
        structural, cost, _order, state = heappop(frontier)
        if best is not None and best.rank <= (structural, cost):
            break
        candidate = search.evaluate(state)
        states_explored += 1
        if candidate.feasible and (best is None or candidate.rank < best.rank):
            best = candidate
        if nearest is None or (candidate.layout.components, *candidate.rank) < (
            nearest.layout.components,
            *nearest.rank,
        ):
            nearest = candidate
        expansion = search.successors(candidate, search_budget - states_explored)
        guided = guided or expansion.guided
        for bound, bound_cost, successor in expansion.successors:
            if successor in seen:
                continue
            seen.add(successor)
            if best is not None and best.rank < (bound, bound_cost):
                continue
            heappush(frontier, (bound, bound_cost, next(serial), successor))

    selected = best or nearest
    assert selected is not None
    exhausted = guided or (bool(frontier) and states_explored >= search_budget)
    events: tuple[PlanEvent, ...] = ()
    if exhausted:
        events = (
            PlanEvent(
                code="planner.search_fallback",
                path="$",
                message=(
                    f"Layout search used its bounded fallback within {search_budget} evaluations; "
                    "selected the best incumbent"
                ),
                severity=PlanSeverity.WARNING,
            ),
        )
    return replace(
        selected,
        semantic=replace(
            selected.semantic,
            events=events + selected.semantic.events,
            states_explored=states_explored,
            search_fallback=exhausted,
        ),
    )


def plan(
    rendered: DocumentLike,
    *,
    target: TargetProfile,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
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
        palette=palette,
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
            palette=palette,
            session=presentation,
            pages=broker,
            capabilities=target.capabilities,
            strategies=dict(cached.strategies),
            fallbacks=dict(cached.fallbacks),
        )
        lowered = _lower_children(semantic.nodes, target, limits)
        assets = _merge_assets(document.assets, semantic.assets)
        _validate(lowered, limits)
        selected_nodes = resolve_variants(lowered, dict(cached.variant_positions))
        converter = _collect_cached_bindings(selected_nodes, cached.scene, nav, chrome)
        resources = {f"asset:{asset.key}": asset for asset in assets}
        return PlanResult(
            scene=cached.scene,
            bindings=converter.bindings,
            form_bindings=converter.form_bindings,
            report=cached.report,
            resources=resources,
            metrics=PlanMetrics(
                states_explored=cached.states_explored,
                cache_hit=True,
                search_fallback=cached.search_fallback,
            ),
            session_updates=cached.session_updates,
        )

    selected = _search(
        _Search(
            document=document,
            target=target,
            limits=limits,
            chrome=chrome,
            localization=localization,
            palette=palette,
            presentation=presentation,
            reservation=reservation,
            positions=positions,
            nav=nav,
        ),
        search_budget=search_budget,
    )
    broker = selected.broker
    semantic = selected.semantic
    assets = _merge_assets(document.assets, semantic.assets)
    # Ladders are decided; everything downstream, root pagination included, sees one tree.
    lowered = resolve_variants(selected.lowered, dict(selected.state.variants))
    measured = selected.layout
    root_events: tuple[PlanEvent, ...] = ()
    if measured.components > limits.total_components:
        local_pagers = [*broker.pagers, *measured.pagers]
        if local_pagers:
            keys = ", ".join(repr(pager.key) for pager in local_pagers)
            message = (
                f"{measured.components} components exceed target maximum {limits.total_components} after local "
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
            message = f"{measured.components} components exceed target maximum {limits.total_components}; {remedy}"
            raise UnsolvableLayoutError(message)
        measured, root_pages = _root_paginate(
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
    _reconcile_pagers(measured, broker)
    if strict and measured.notes:
        raise LayoutDegradedError("; ".join(note.message for note in measured.notes))
    hard_failures = measured.failures
    if hard_failures:
        message = "; ".join(note.message for note in hard_failures)
        raise UnsolvableLayoutError(message)
    converter = _Converter()
    scene = SceneDocument(
        protocol=SceneCodec.protocol,
        target=target.id,
        target_version=target.version,
        children=converter.children(measured.children),
        assets=tuple(SceneAsset(asset.key, asset.name, asset.media_type) for asset in assets),
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
            for note in measured.notes
        ),
        logical_fingerprint=stable_fingerprint((document,)),
        scene_fingerprint=fingerprint,
    )
    resources = dict(converter.resources)
    resources.update({f"asset:{asset.key}": asset for asset in assets})
    updates = semantic.updates + broker.updates
    result = PlanResult(
        scene=scene,
        bindings=converter.bindings,
        form_bindings=converter.form_bindings,
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
                selected.state.strategies,
                semantic.states_explored,
                semantic.search_fallback,
                selected.state.variants,
                selected.state.fallbacks,
            ),
        )
    return result


def _reconcile_pagers(measured: MeasuredLayout, broker: CursorCoordinator) -> None:
    """Move each measured pager to the page its reader belongs on.

    Measuring a page means rendering *some* page, and the choice is made before anyone knows
    how many there are — the count is an output of fitting. This is the first moment a
    stored cursor can be reconciled against it, and because the page is a projection it
    costs a slot rewrite rather than a second measurement. The mount used to do this by
    drawing twice.
    """
    positions: dict[str, Position] = {}
    for pager in measured.pagers:
        request = MaterializedCursorRequest(
            key=pager.key,
            extent=pager.pages,
            fingerprint=content_fingerprint(pager.fragments),
            initial="end" if pager.initial else "start",
        )
        grant = broker.grant(request)
        broker.record(request, grant.position)
        positions[pager.key] = grant.position
    measured.reposition(positions)


def _root_paginate(
    nodes: Sequence[Node],
    *,
    key: str,
    target_limits: V2Limits,
    chrome: Chrome,
    reserved_text: int,
    nav: PlannedNav,
    broker: CursorCoordinator,
) -> tuple[MeasuredLayout, int]:
    maximum_pages = max(1, len(nodes))

    def measure_page(children: Sequence[Node], index: int, pages: int) -> MeasuredLayout:
        chrome_nodes: tuple[Node, ...] = (
            Footer(chrome.page_footer(index + 1, pages), overflow=Never()),
            *nav(materialized_navigation_state(key, Position(offset=index), pages, chrome)),
        )
        return measure(
            (*children, *chrome_nodes),
            limits=target_limits,
            chrome=chrome,
            strict=False,
            reserved_text=reserved_text,
            nav=nav,
        )

    # Greedy: grow a page until it stops fitting, then cut. Every probe measures a
    # different prefix, so there is nothing to memoize — the cost is one measurement per
    # node, paid only by a document that already blew the component budget. These probes
    # are packing, not optimization, so they stay out of the search's evaluation count.
    pages: list[tuple[Node, ...]] = []
    current: tuple[Node, ...] = ()
    for node in nodes:
        candidate = (*current, node)
        probe = measure_page(candidate, 0, maximum_pages)
        if current and (probe.components > target_limits.total_components or probe.overflowed):
            pages.append(current)
            current = (node,)
            probe = measure_page(current, 0, maximum_pages)
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
    return measure_page(pages[index], index, grant.extent), grant.extent


def _plan_cache_key(
    nodes: Sequence[object],
    *,
    target: TargetProfile,
    limits: V2Limits,
    chrome: Chrome,
    localization: Localization,
    palette: Palette,
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
        "palette": stable_value(palette),
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

"""Plan logical documents into immutable target-resolved scenes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from heapq import heappop, heappush
from itertools import count
from typing import cast

from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome, localize_chrome
from squid_layouts.document import Document, DocumentLike, as_document
from squid_layouts.errors import LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
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
from squid_layouts.planning.dialect import SceneBindings, TargetDialect
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
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    lossy_notes,
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
from squid_layouts.planning.v2 import V2_DIALECT
from squid_layouts.primitives.nodes import (
    Break,
    Budget,
    Button,
    Extension,
    Node,
    Panel,
    RawItem,
    Row,
    Section,
    SelectMenu,
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
    SceneDocument,
)
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization

EMPTY_RESERVATION = ResourceCost()


def _dialect_for(target: TargetProfile) -> TargetDialect:
    """A target's shape, defaulting to Components V2 for a profile that names none."""
    dialect = target.dialect
    if dialect is None:
        return V2_DIALECT
    return cast(TargetDialect, dialect)


def _merge_assets(*groups: Sequence[Asset]) -> tuple[Asset, ...]:
    merged: dict[str, Asset] = {}
    for asset in (asset for group in groups for asset in group):
        existing = merged.get(asset.key)
        if existing is not None and existing != asset:
            message = f"asset key {asset.key!r} identifies two different assets"
            raise LayoutInvariantError(message)
        merged.setdefault(asset.key, asset)
    return tuple(merged.values())


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
    capacities: Mapping[str, int]

    @property
    def degradation(self) -> DegradationProfile:
        return self.layout.degradation

    @property
    def feasible(self) -> bool:
        return self.layout.fits(self.capacities) and not self.layout.failures

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
    dialect: TargetDialect
    limits: V2Limits
    chrome: Chrome
    localization: Localization
    palette: Palette
    presentation: PresentationSession
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
        lowered = self.dialect.normalize(semantic.nodes, self.target, self.limits)
        self.dialect.validate(lowered, self.limits)
        variants = canonical_positions(lowered, dict(state.variants))
        steps = [*_fallback_notes(decisions.fallbacks, fallbacks), *variant_notes(lowered, variants)]
        layout = measure(
            resolve_variants(lowered, variants),
            limits=self.limits,
            chrome=self.chrome,
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
            self.target.capacities,
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


@dataclass(slots=True)
class _ParetoArchive:
    """The non-dominated states seen so far, in fidelity and per-axis resource cost.

    A state is worth expanding only if it reaches somewhere no explored state already
    reaches more cheaply. "More cheaply" is per axis and never traded: a candidate that
    spends fewer components but more embed text is not dominated by one that does the
    reverse, because a document blocked on components and a document blocked on embed text
    need different steps to become feasible. Collapsing that to a scalar is exactly how a
    component-only heuristic starves a second budget.
    """

    points: list[tuple[DegradationProfile, ResourceCost]] = field(default_factory=list)

    def admit(self, candidate: _Candidate) -> bool:
        """Record this candidate and report whether anything already dominates it."""
        point = (candidate.degradation, candidate.layout.cost)
        for degradation, cost in self.points:
            if degradation <= point[0] and not point[1].cheaper_anywhere(cost):
                return False
        self.points = [
            existing
            for existing in self.points
            if not (point[0] <= existing[0] and not existing[1].cheaper_anywhere(point[1]))
        ]
        self.points.append(point)
        return True


def _overspend(candidate: _Candidate) -> int:
    """How far past its budgets a candidate is, summed over every axis it overspends.

    Only used to keep the least-bad infeasible answer when nothing fits, so a single
    comparable number is enough — the report already names the axes individually.
    """
    return sum(spent - capacity for _axis, spent, capacity in candidate.layout.cost.over(candidate.capacities))


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
        # A semantic fallback branch is the author naming a *lesser* representation, so it
        # stays loss. A primitive ladder rung only says "another shape"; fidelity says whether
        # that shape costs anything.
        SolveNote(
            SolveNoteCode.SEMANTIC_FALLBACK,
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

    archive = _ParetoArchive()

    while frontier and states_explored < search_budget:
        structural, cost, _order, state = heappop(frontier)
        if best is not None and best.rank <= (structural, cost):
            break
        candidate = search.evaluate(state)
        states_explored += 1
        if candidate.feasible and (best is None or candidate.rank < best.rank):
            best = candidate
        if nearest is None or (_overspend(candidate), *candidate.rank) < (_overspend(nearest), *nearest.rank):
            nearest = candidate
        if not archive.admit(candidate):
            # Dominated: no better on fidelity and no cheaper on any axis than something
            # already explored. Its successors are reachable from that one for less, so
            # expanding it would only re-walk the same ground.
            continue
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
    # Every axis is withheld the same way: by planning against a smaller target.
    target = target.reserve(reservation)
    dialect = _dialect_for(target)
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
        lowered = dialect.normalize(semantic.nodes, target, limits)
        assets = _merge_assets(document.assets, semantic.assets)
        dialect.validate(lowered, limits)
        selected_nodes = resolve_variants(lowered, dict(cached.variant_positions))
        collected = _collect_cached_bindings(selected_nodes, cached.scene, nav, chrome)
        resources = {f"asset:{asset.key}": asset for asset in assets}
        return PlanResult(
            scene=cached.scene,
            bindings=collected.bindings,
            form_bindings=collected.form_bindings,
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
            dialect=dialect,
            limits=limits,
            chrome=chrome,
            localization=localization,
            palette=palette,
            presentation=presentation,
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
    capacities = target.capacities
    overspent = target.over_capacity(measured.cost)
    if overspent:
        # Every offending axis, not the first: a document over two budgets should hear about
        # both rather than come back round the loop to discover the second.
        blown = "; ".join(f"{spent} {axis} exceed target maximum {capacity}" for axis, spent, capacity in overspent)
        local_pagers = [*broker.pagers, *measured.pagers]
        if local_pagers:
            keys = ", ".join(repr(pager.key) for pager in local_pagers)
            message = (
                f"{blown} after local pagination ({keys}). Local and root pagination are never simultaneous; "
                "fold component groups, split the document, or move the long local collection onto its own screen."
            )
            raise UnsolvableLayoutError(message)
        if document.key is None or nav is None:
            remedy = (
                "give Document an explicit key and plan with navigation controls to allow root pagination"
                if document.key is None
                else "plan with navigation controls or split the static document"
            )
            message = f"{blown}; {remedy}"
            raise UnsolvableLayoutError(message)
        measured, root_pages = dialect.paginate(
            lowered,
            key=document.key,
            capacities=capacities,
            limits=limits,
            chrome=chrome,
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
    if strict and (lossy := lossy_notes(measured.notes)):
        raise LayoutDegradedError("; ".join(note.message for note in lossy))
    hard_failures = measured.failures
    if hard_failures:
        message = "; ".join(note.message for note in hard_failures)
        raise UnsolvableLayoutError(message)
    bindings = SceneBindings()
    scene = SceneDocument(
        protocol=SceneCodec.protocol,
        target=target.id,
        target_version=target.version,
        body=dialect.body(measured.children, bindings),
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
                severity=(
                    PlanSeverity.ADAPTATION
                    if note.severity is SolveNoteSeverity.ADAPTATION
                    else PlanSeverity.DEGRADATION
                ),
            )
            for note in measured.notes
        ),
        logical_fingerprint=stable_fingerprint((document,)),
        scene_fingerprint=fingerprint,
    )
    resources = dict(bindings.resources)
    resources.update({f"asset:{asset.key}": asset for asset in assets})
    updates = semantic.updates + broker.updates
    result = PlanResult(
        scene=scene,
        bindings=bindings.bindings,
        form_bindings=bindings.form_bindings,
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


def _collect_bindings(nodes: Sequence[Node]) -> SceneBindings:
    collected = SceneBindings()

    def collect(node: Node) -> None:
        match node:
            case Button() | SelectMenu():
                collected.action(node)
            case Row(items=items):
                for item in items:
                    if isinstance(item, Button):
                        collected.action(item)
            case Section(accessory=accessory):
                if isinstance(accessory, Button):
                    collected.action(accessory)
            case Panel(children=children) | Budget(children=children) | Break(children=children):
                for child in children:
                    collect(child)
            case _:
                return

    for node in nodes:
        collect(node)
    return collected


def _collect_cached_bindings(
    nodes: Sequence[Node],
    scene: SceneDocument,
    nav: PlannedNav | None,
    chrome: Chrome,
) -> SceneBindings:
    collected = _collect_bindings(nodes)
    if nav is None:
        return collected
    for pager in scene.pagers:
        generated = _collect_bindings(
            nav(materialized_navigation_state(pager.key, Position(offset=pager.page), pager.pages, chrome))
        )
        for key, binding in generated.bindings.items():
            collected.bindings.setdefault(key, binding)
    return collected

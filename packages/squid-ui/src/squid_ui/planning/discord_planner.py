"""Discord semantic adaptation, layout search, measurement, and scene conversion."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, is_dataclass, replace
from enum import Enum
from heapq import heappop, heappush
from itertools import count
from typing import Any, cast

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.chrome import Chrome, localize_chrome
from squid_ui.document import Document, DocumentLike, as_document
from squid_ui.errors import LayoutDegradedError, LayoutInvariantError, UnsolvableLayoutError
from squid_ui.palette import Palette
from squid_ui.planning.cache import CachedPlan, PlanCache, PlanMemo
from squid_ui.planning.cursors import CursorCoordinator, MaterializedCursorRequest, content_fingerprint
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.planning.discord_dialect import DiscordDialect, SceneBindings
from squid_ui.planning.frontier import (
    VariantPath,
    canonical_positions,
    guided_step,
    resolve_variants,
    steppable,
    variant_notes,
    variant_profile,
    variant_state_bound,
)
from squid_ui.planning.generated import GeneratedHandler
from squid_ui.planning.identity import stable_fingerprint, stable_value
from squid_ui.planning.layout_measurement.diagnostics import (
    SolveNote,
    SolveNoteCode,
    SolveNoteSeverity,
    lossy_notes,
)
from squid_ui.planning.layout_measurement.solver import (
    MeasuredLayout,
    measure,
)
from squid_ui.planning.limits import Axis, MessageLimits
from squid_ui.planning.navigation import PlannedNav, materialized_navigation_state
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import ResourceCost
from squid_ui.planning.search import (
    CostVector,
    StrategyAxis,
    assignment_cost,
    ranked_candidates,
)
from squid_ui.planning.semantic_adaptation.decisions import nominate_decisions
from squid_ui.planning.semantic_adaptation.lowering import (
    lower_semantics,
)
from squid_ui.planning.semantic_adaptation.model import FallbackAxis, SemanticDecisions, SemanticLowering
from squid_ui.planning.target import Target
from squid_ui.primitives.constraints import Paginate
from squid_ui.primitives.nodes import (
    Break,
    Budget,
    Button,
    Card,
    EntitySelect,
    Extension,
    Node,
    Panel,
    RawItem,
    Row,
    Section,
    SelectMenu,
    Variants,
)
from squid_ui.primitives.nodes import Code as PrimitiveCode
from squid_ui.primitives.nodes import Footer as PrimitiveFooter
from squid_ui.primitives.nodes import Heading as PrimitiveHeading
from squid_ui.primitives.nodes import Text as PrimitiveText
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.scene.model import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanReuse, PlanSeverity
from squid_ui.semantic import Code as SemanticCode
from squid_ui.semantic import Paragraph as SemanticParagraph
from squid_ui.sources import Position
from squid_ui.text import Localization


@dataclass(frozen=True, slots=True)
class _DynamicSlot:
    index: int


@dataclass(frozen=True, slots=True)
class _GeneratedHandlerTemplate:
    handler_type: type[GeneratedHandler[Any]]
    values: tuple[tuple[str, object], ...]


class _UnboundDynamic(Exception):
    pass


def _walk_value(value: object, visit: Callable[[object], None]) -> None:
    if callable(value):
        visit(value)
        return
    if isinstance(value, Enum | str | bytes | int | float | bool) or value is None:
        return
    if is_dataclass(value) and not isinstance(value, type):
        for item in fields(value):
            _walk_value(getattr(value, item.name), visit)
        return
    if isinstance(value, Mapping):
        for key, item in sorted(value.items(), key=lambda entry: str(entry[0])):
            _walk_value(key, visit)
            _walk_value(item, visit)
        return
    if isinstance(value, Sequence):
        for item in value:
            _walk_value(item, visit)
        return
    visit(value)


def _dynamic_values(value: object) -> tuple[object, ...]:
    found: list[object] = []
    _walk_value(value, found.append)
    return tuple(found)


def _program_dynamic_values(
    document: Document[Any],
    *,
    target: object,
    limits: object,
    chrome: Chrome,
    localization: object,
    palette: object,
    presentation: object,
    positions: object,
    nav: PlannedNav | None,
    capabilities: object,
    planned: scene.Scene[Any],
) -> tuple[object, ...]:
    external = (
        target,
        limits,
        chrome,
        localization,
        palette,
        presentation,
        positions,
        nav,
        capabilities,
    )
    dynamic = tuple(value for value in external if value is not None) + _dynamic_values(document)
    if nav is not None:
        for pager in planned.pagers:
            nodes = nav(materialized_navigation_state(pager.key, Position(offset=pager.page), pager.pages, chrome))
            dynamic += _dynamic_values(nodes)
    return dynamic


def _dynamic_index(value: object, dynamic: tuple[object, ...]) -> int | None:
    if isinstance(value, Enum | str | bytes | int | float | bool) or value is None:
        return None
    return next((index for index, current in enumerate(dynamic) if current is value), None)


def _template(value: object, dynamic: tuple[object, ...]) -> object:
    if (index := _dynamic_index(value, dynamic)) is not None:
        return _DynamicSlot(index)
    if isinstance(value, GeneratedHandler):
        return _GeneratedHandlerTemplate(
            type(value),
            tuple(
                (item.name, _template(getattr(value, item.name), dynamic))
                for item in fields(cast(Any, value))
                if item.init
            ),
        )
    if callable(value) or (
        not isinstance(value, Enum | str | bytes | int | float | bool | Mapping | Sequence)
        and value is not None
        and not (is_dataclass(value) and not isinstance(value, type))
    ):
        raise _UnboundDynamic
    if isinstance(value, Enum | str | bytes | int | float | bool) or value is None:
        return value
    if is_dataclass(value) and not isinstance(value, type):
        changes = {item.name: _template(getattr(value, item.name), dynamic) for item in fields(value) if item.init}
        return replace(value, **changes)
    if isinstance(value, Mapping):
        return {_template(key, dynamic): _template(item, dynamic) for key, item in value.items()}
    if isinstance(value, Sequence):
        return tuple(_template(item, dynamic) for item in value)
    raise _UnboundDynamic


def _materialize(value: object, dynamic: tuple[object, ...]) -> object:
    if isinstance(value, _DynamicSlot):
        return dynamic[value.index]
    if isinstance(value, _GeneratedHandlerTemplate):
        return value.handler_type(
            **{name: _materialize(item, dynamic) for name, item in value.values},
        )
    if isinstance(value, Enum | str | bytes | int | float | bool) or value is None:
        return value
    if is_dataclass(value) and not isinstance(value, type):
        changes = {item.name: _materialize(getattr(value, item.name), dynamic) for item in fields(value) if item.init}
        return replace(value, **changes)
    if isinstance(value, Mapping):
        return {_materialize(key, dynamic): _materialize(item, dynamic) for key, item in value.items()}
    if isinstance(value, Sequence):
        return tuple(_materialize(item, dynamic) for item in value)
    return value


def _compile_template(nodes: Sequence[Node], dynamic: tuple[object, ...]) -> object | None:
    try:
        return _template(tuple(nodes), dynamic)
    except _UnboundDynamic:
        return None


def _declared_assets(document: Document[Any]) -> tuple[Asset, ...]:
    found: list[Asset] = list(document.assets)

    def walk(value: object) -> None:
        if isinstance(value, Asset):
            found.append(value)
            return
        if is_dataclass(value) and not isinstance(value, type):
            for item in fields(value):
                walk(getattr(value, item.name))
            return
        if isinstance(value, Mapping):
            for key, item in value.items():
                walk(key)
                walk(item)
            return
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            for item in value:
                walk(item)

    walk(document.children)
    return _merge_assets(found)


def _incremental_shape(document: Document[Any]) -> str | None:
    """Identify one independent text region while excluding pagination and shared allocation."""
    if document.assets or len(document.children) != 1:
        return None
    node = document.children[0]
    match node:
        case SemanticParagraph(importance=importance):
            shape: object = ("semantic.paragraph", importance)
        case SemanticCode(language=language):
            shape = ("semantic.code", language)
        case PrimitiveText(overflow=overflow, priority=priority):
            if isinstance(overflow, Paginate):
                return None
            shape = ("primitive.text", overflow, priority)
        case PrimitiveHeading(level=level, overflow=overflow, priority=priority):
            if isinstance(overflow, Paginate):
                return None
            shape = ("primitive.heading", level, overflow, priority)
        case PrimitiveFooter(overflow=overflow, priority=priority):
            if isinstance(overflow, Paginate):
                return None
            shape = ("primitive.footer", overflow, priority)
        case PrimitiveCode(lang=language, overflow=overflow, priority=priority):
            if isinstance(overflow, Paginate):
                return None
            shape = ("primitive.code", language, overflow, priority)
        case _:
            return None
    return stable_fingerprint(((document.key, shape),))


def _certifies_incremental(candidate: _Candidate) -> bool:
    """Whether this preferred state is an isolated, lossless fit with no choices to revisit."""
    return (
        candidate.feasible
        and candidate.degradation.lossless
        and not candidate.layout.overflowed
        and not candidate.layout.notes
        and not candidate.layout.pagers
        and not candidate.broker.pagers
        and not candidate.decisions.strategies
        and not candidate.decisions.fallbacks
        and not candidate.state.strategies
        and not candidate.state.fallbacks
        and not steppable(candidate.lowered, dict(candidate.state.variants))
    )


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
    capacities: Mapping[Axis, int]

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
class _Compilation[BodyT: scene.Body, RenderTargetT, AdapterT]:
    """One document compiled for one target: everything that does not vary between candidates.

    The target is already reserved and the chrome already localized, which is the whole reason
    this is a value rather than the request itself. Everything else is read back off the
    request, so a search candidate, a structural replay and the final assembly all see one
    set of inputs instead of three re-spreadings of the same eight fields.
    """

    request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    document: Document[RenderTargetT]
    target: Target[Any, BodyT, RenderTargetT, AdapterT]
    chrome: Chrome

    @classmethod
    def resolve[ResolvedBodyT: scene.Body, ResolvedRenderTargetT, ResolvedAdapterT](
        cls,
        document: Document[ResolvedRenderTargetT],
        request: PlanRequest[ResolvedBodyT, ResolvedRenderTargetT, ResolvedAdapterT],
    ) -> _Compilation[ResolvedBodyT, ResolvedRenderTargetT, ResolvedAdapterT]:
        """Withhold the reservation and localize the chrome, once, for every phase below."""
        # Every axis is withheld the same way: by planning against a smaller target.
        return _Compilation(
            request=request,
            document=document,
            target=request.target.reserve(request.reservation),
            chrome=localize_chrome(request.chrome, request.localization),
        )

    @property
    def limits(self) -> MessageLimits:
        return self.target.limits

    @property
    def dialect(self) -> DiscordDialect[MessageLimits, BodyT, RenderTargetT]:
        return cast(DiscordDialect[MessageLimits, BodyT, RenderTargetT], self.target.dialect)

    @property
    def localization(self) -> Localization:
        return self.request.localization

    @property
    def palette(self) -> Palette:
        return self.request.palette

    @property
    def presentation(self) -> PresentationState:
        return self.request.presentation

    @property
    def positions(self) -> Mapping[str, Position] | None:
        return self.request.positions

    @property
    def nav(self) -> PlannedNav | None:
        return self.request.nav

    def coordinator(self) -> CursorCoordinator:
        return CursorCoordinator(self.presentation, self.chrome, self.nav, self.positions)

    def replay(self, cached: CachedPlan[BodyT]) -> PlanResult[BodyT]:
        """Rebuild a result around a cached scene, recovering only what could not be cached.

        The scene, its report and its staged session updates come back untouched. Callbacks
        cannot be cached, so the primitive tree carrying them is recovered first -- from the
        compiled template when it materializes, and by re-lowering under the cached decisions
        when it does not. Both routes reach the same bindings; only their cost differs.
        """
        nodes = self._materialized(cached)
        if nodes is not None:
            assets: Sequence[Asset] = _declared_assets(self.document)
        else:
            broker = self.coordinator()
            semantic = lower_semantics(
                self.document.children,
                limits=self.limits,
                chrome=self.chrome,
                localization=self.localization,
                palette=self.palette,
                session=self.presentation,
                pages=broker,
                capabilities=self.target.capabilities,
                strategies=dict(cached.strategies),
                fallbacks=dict(cached.fallbacks),
            )
            lowered = self.dialect.normalize(semantic.nodes, self.target)
            assets = _merge_assets(self.document.assets, semantic.assets)
            self.dialect.validate(lowered, self.limits)
            nodes = tuple(resolve_variants(lowered, dict(cached.variant_positions)))
        collected = _collect_cached_bindings(nodes, cached.scene, self.nav, self.chrome)
        return PlanResult(
            scene=cached.scene,
            bindings=collected.bindings,
            form_bindings=collected.form_bindings,
            report=cached.report,
            resources={f"asset:{asset.key}": asset for asset in assets},
            metrics=PlanMetrics(
                states_explored=cached.states_explored,
                cache_hit=True,
                reuse=PlanReuse.STRUCTURAL,
                search_fallback=cached.search_fallback,
            ),
            session_updates=cached.session_updates,
        )

    def _materialized(self, cached: CachedPlan[BodyT]) -> tuple[Node, ...] | None:
        """The cached primitive tree with this render's live values bound back into it."""
        if cached.lowered_template is None:
            return None
        dynamic = self.dynamic_values(cached.scene)
        try:
            restored = _materialize(cached.lowered_template, dynamic)
        except _UnboundDynamic, IndexError, TypeError, ValueError:
            return None
        return cast(tuple[Node, ...], restored) if isinstance(restored, tuple) else None

    def dynamic_values(self, planned: scene.Scene[BodyT]) -> tuple[object, ...]:
        """The process-local values a compiled template holds slots for."""
        return _program_dynamic_values(
            self.document,
            target=self.target,
            limits=self.limits,
            chrome=self.chrome,
            localization=self.localization,
            palette=self.palette,
            presentation=self.presentation,
            positions=self.positions,
            nav=self.nav,
            capabilities=self.target.capabilities,
            planned=planned,
        )

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
        lowered = self.dialect.normalize(semantic.nodes, self.target)
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
            effect = (
                DegradationEffect(priority=occurrence.priority, path=occurrence.path, dropped_nodes=1)
                if occurrence.optional
                else DegradationEffect(priority=occurrence.priority, path=occurrence.path, semantic_steps=rung)
            )
            profile = profile.with_effect(effect)
    return profile


def _fallback_notes(occurrences: Sequence[FallbackAxis], selected: Mapping[str, int]) -> list[SolveNote]:
    notes: list[SolveNote] = []
    for occurrence in occurrences:
        for step in range(selected.get(occurrence.path, 0)):
            if occurrence.optional:
                notes.append(
                    SolveNote(
                        SolveNoteCode.OPTIONAL_DROPPED,
                        f"{occurrence.path} omitted optional region "
                        f"(priority {occurrence.priority}) under layout pressure",
                    )
                )
            else:
                # A semantic fallback branch is the author naming a lesser representation,
                # so it stays loss even though the primitive shape may be exact.
                notes.append(
                    SolveNote(
                        SolveNoteCode.SEMANTIC_FALLBACK,
                        f"{occurrence.path} stepped to variant {step + 2} of {occurrence.branches} "
                        f"(priority {occurrence.priority}) under layout pressure",
                    )
                )
    return notes


def _search(search: _Compilation[Any, Any, Any], *, search_budget: int) -> _Candidate:
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


def plan[RenderTargetT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    *,
    cache: PlanCache[BodyT] | None = None,
    memo: PlanMemo[BodyT] | None = None,
) -> PlanResult[BodyT]:
    """Resolve a complete logical document for one target.

    Planning owns every fit and fallback decision. The resulting scene contains visual action
    references, while callbacks remain in the plan result for the mounted frontend.

    Three ways in, cheapest first: an exact memo hit returns the previous result outright, a
    structural cache hit replays a stored scene, and a miss runs the layout search.
    """
    exact_key = request.exact_key()
    if memo is not None and (exact := memo.replay(rendered, exact_key, request.presentation)) is not None:
        return exact
    compilation = _Compilation.resolve(as_document(rendered), request)
    cache_context = request.cache_context(target=compilation.target, chrome=compilation.chrome)
    cache_key = _plan_cache_key((compilation.document,), context=cache_context)
    incremental_shape = None if cache is None else _incremental_shape(compilation.document)
    incremental_key = (
        None
        if cache is None or incremental_shape is None
        else _plan_cache_key(("incremental", incremental_shape), context=cache_context)
    )
    cached = cache.get(cache_key) if cache is not None else None
    if cached is not None:
        result = compilation.replay(cached)
        if memo is not None:
            memo.store(rendered, exact_key, request.presentation, result)
        return result
    return _compile(
        compilation,
        rendered=rendered,
        exact_key=exact_key,
        cache=cache,
        cache_key=cache_key,
        incremental_key=incremental_key,
        memo=memo,
    )


def _compile[RenderTargetT, AdapterT, BodyT: scene.Body](
    compilation: _Compilation[BodyT, RenderTargetT, AdapterT],
    *,
    rendered: DocumentLike[RenderTargetT],
    exact_key: tuple[object, ...],
    cache: PlanCache[BodyT] | None,
    cache_key: str,
    incremental_key: str | None,
    memo: PlanMemo[BodyT] | None,
) -> PlanResult[BodyT]:
    """Search the layout space, assemble the scene, and record what may be reused."""
    request = compilation.request
    document = compilation.document
    target = compilation.target
    dialect = compilation.dialect
    limits = compilation.limits
    chrome = compilation.chrome
    nav = compilation.nav
    presentation = compilation.presentation
    reuse = PlanReuse.MISS
    selected: _Candidate | None = None
    if cache is not None and incremental_key is not None and cache.admits_incremental(incremental_key):
        candidate = compilation.evaluate(_State())
        if _certifies_incremental(candidate):
            selected = replace(candidate, semantic=replace(candidate.semantic, states_explored=1))
            reuse = PlanReuse.INCREMENTAL
    if selected is None:
        selected = _search(compilation, search_budget=request.search_budget)
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
    if request.strict and (lossy := lossy_notes(measured.notes)):
        raise LayoutDegradedError("; ".join(note.message for note in lossy))
    hard_failures = measured.failures
    if hard_failures:
        message = "; ".join(note.message for note in hard_failures)
        raise UnsolvableLayoutError(message)
    bindings = SceneBindings()
    body = dialect.body(measured.children, bindings)
    if not isinstance(body, target.body_type):
        message = (
            f"target {target.triple!r} declared {target.body_type.__name__}, "
            f"but its dialect produced {type(body).__name__}"
        )
        raise LayoutInvariantError(message)
    planned = scene.Scene(
        protocol=scene.Codec.protocol,
        target=target.id,
        target_version=target.version,
        body=body,
        assets=tuple(scene.Asset(asset.key, asset.name, asset.media_type) for asset in assets),
        pagers=broker.pagers,
    )
    fingerprint = scene.Codec.fingerprint(planned)
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
        logical_fingerprint=stable_fingerprint((planned,)),
        scene_fingerprint=fingerprint,
    )
    resources = dict(bindings.resources)
    resources.update({f"asset:{asset.key}": asset for asset in assets})
    updates = semantic.updates + broker.updates
    result = PlanResult(
        scene=planned,
        bindings=bindings.bindings,
        form_bindings=bindings.form_bindings,
        report=report,
        resources=resources,
        metrics=PlanMetrics(
            states_explored=semantic.states_explored,
            cache_hit=reuse is PlanReuse.INCREMENTAL,
            reuse=reuse,
            search_fallback=semantic.search_fallback,
        ),
        session_updates=updates,
    )
    if cache is not None and _cacheable(lowered):
        cache.put(
            cache_key,
            CachedPlan(
                planned,
                report,
                updates,
                selected.state.strategies,
                semantic.states_explored,
                semantic.search_fallback,
                selected.state.variants,
                selected.state.fallbacks,
                _compile_template(lowered, compilation.dynamic_values(planned)),
            ),
        )
        if incremental_key is not None and _certifies_incremental(selected) and not root_events:
            cache.certify_incremental(incremental_key)
    if memo is not None and _cacheable(lowered):
        memo.store(rendered, exact_key, presentation, result)
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


def _plan_cache_key(nodes: Sequence[object], *, context: Mapping[str, object]) -> str:
    relevant = {"document": stable_value(nodes), **context}
    return stable_fingerprint((relevant,))


def _cacheable(nodes: Sequence[Node]) -> bool:
    def check(node: Node) -> bool:
        if isinstance(node, Extension | RawItem):
            return False
        if isinstance(node, Variants):
            return all(check(child) for variant in node.variants for child in variant.nodes)
        if isinstance(node, Panel | Budget | Break | Card):
            return all(check(child) for child in node.children)
        return True

    return all(check(node) for node in nodes)


def _collect_bindings(nodes: Sequence[Node]) -> SceneBindings:
    collected = SceneBindings()

    def collect(node: Node) -> None:
        match node:
            case Button() | SelectMenu() | EntitySelect():
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
    document: scene.Scene,
    nav: PlannedNav | None,
    chrome: Chrome,
) -> SceneBindings:
    collected = _collect_bindings(nodes)
    if nav is None:
        return collected
    for pager in document.pagers:
        generated = _collect_bindings(
            nav(materialized_navigation_state(pager.key, Position(offset=pager.page), pager.pages, chrome))
        )
        for key, binding in generated.bindings.items():
            collected.bindings.setdefault(key, binding)
    return collected


class DiscordPlanner:
    """The complete planner backend for both Discord message dialects."""

    plan = staticmethod(plan)


DISCORD_PLANNER = DiscordPlanner()

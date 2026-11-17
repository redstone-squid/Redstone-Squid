"""Public fixed and automatic composition through typed target rules."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from types import MappingProxyType

from squid_ui import scene
from squid_ui.authoring.document import Document, DocumentLike, as_document
from squid_ui.authoring.semantic import Group
from squid_ui.content.assets import Asset
from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.planning.allocation_policy import SearchCompletion, SearchDiagnostics
from squid_ui.planning.binding_preparation import prepare_bindings
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.composition import (
    BuiltComposition,
    CandidateViolation,
    OccurrenceId,
    PlanningContext,
    SemanticOccurrence,
    TargetRules,
    UnsupportedCapability,
    WorkBudget,
    build_composition,
)
from squid_ui.planning.composition_input import prepare_composition
from squid_ui.planning.composition_request import CompositionOptions, TargetComposition
from squid_ui.planning.composition_state import RetainedComposition
from squid_ui.planning.identity import has_routed_page, stable_fingerprint
from squid_ui.planning.joint_search import JointExhaustedError, JointUnsupportedError, search_composition
from squid_ui.planning.offers import select_composition
from squid_ui.planning.pagination.navigation import PagerNav, PlannedNav
from squid_ui.planning.request import DEFAULT_SEARCH_BUDGET, PlanRequest
from squid_ui.planning.result import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanSeverity
from squid_ui.planning.semantic_index import UnsupportedSemanticError, semantic_graph
from squid_ui.planning.snapshot import PlanningSnapshot

_logger = logging.getLogger(__name__)


class CompositionRejectedError(LayoutError):
    """The explicit candidate failed; other choices have not been searched."""

    def __init__(self, violations: tuple[CandidateViolation, ...]) -> None:
        self.violations = violations
        super().__init__(f"explicit composition rejected: {violations!r}")


@dataclass(frozen=True, slots=True)
class ResolvedComposition:
    """The automatic or fixed execution inputs after defaults are resolved."""

    automatic: bool
    work_limit: int
    navigation: PlannedNav | None
    mounted: bool

    @classmethod
    def for_options[BodyT: scene.Body, RenderTargetT, AdapterT](
        cls,
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions | None,
    ) -> ResolvedComposition:
        automatic = options is not None and options.automatic
        navigation = request.nav
        if navigation is None and request.composition is None:
            navigation = request.target.dialect.navigation
        mounted = (
            request.composition is None
            or request.session is not None
            or navigation is not None
            or request.positions is not None
        )
        work_limit = options.work_limit if options is not None else request.search_budget
        return cls(automatic, work_limit, navigation, mounted)


def _context[BodyT: scene.Body, RenderTargetT, AdapterT](
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    resolved: ResolvedComposition,
) -> PlanningContext:
    effective = request.target.reserve(request.reservation)
    return PlanningContext(
        WorkBudget(resolved.work_limit),
        request.localization,
        request.strict,
        request.palette,
        request.target.capabilities,
        effective.capacities,
        request.chrome,
        PagerNav(resolved.navigation, request.presentation, staged=True) if resolved.navigation is not None else None,
        resolved.mounted,
        escape_text=request.target.adapter.escape_text,
        presentation=request.presentation,
        extensions=request.target.extensions,
        limits=effective.limits,
    )


def _plan[RenderTargetT, AdapterT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT],
    selections: Mapping[OccurrenceId, str],
    options: CompositionOptions | None = None,
    cache: PlanCache | None = None,
) -> PlanResult[BodyT]:
    resolved = ResolvedComposition.for_options(request, options)
    automatic = resolved.automatic
    context = _context(request, resolved)
    document = as_document(rendered)
    context = replace(context, assets=document.assets)
    if not automatic and (document.assets or request.positions or request.nav is not None):
        violation = UnsupportedCapability(
            OccurrenceId(("root",)),
            "composition does not support document assets or mounted pagination inputs; use composition.pages",
        )
        raise CompositionRejectedError((violation,))
    cache_key = ""
    partition_hint = None
    try:
        if cache is not None and automatic:
            assert options is not None
            cache_key = stable_fingerprint(
                (document, replace(options, pages={})), consume=context.work.consume, cache=request.fingerprints
            )
            partition_hint = cache.partition_hint(cache_key)
        graph = semantic_graph(Group(document.children, key=document.key or "@document"), consume=context.work.consume)
    except LayoutSearchExhaustedError as error:
        if not automatic:
            raise
        diagnostics = SearchDiagnostics(
            SearchCompletion.EXHAUSTED, optimal=False, work_used=context.work.spent, work_limit=context.work.limit
        )
        _logger.debug("Joint allocation exhausted during preprocessing: %s", diagnostics)
        raise JointExhaustedError(diagnostics) from error
    except UnsupportedSemanticError as error:
        if automatic:
            raise JointUnsupportedError(
                SearchDiagnostics(
                    SearchCompletion.UNSUPPORTED,
                    optimal=False,
                    work_used=context.work.spent,
                    work_limit=context.work.limit,
                    violations=(UnsupportedCapability(OccurrenceId(("root",)), error.kind),),
                    unsupported=(OccurrenceId(("root",)),),
                )
            ) from error
        raise CompositionRejectedError((UnsupportedCapability(OccurrenceId(("root",)), error.kind),)) from error
    root = next(ref for ref, unit in graph.units.items() if unit.parent is None)
    if automatic:
        assert options is not None

        def finalize(
            built: BuiltComposition[ResourceT, BodyT, PreparationT, object], work_context: PlanningContext
        ) -> PlanResult[BodyT]:
            output = prepare_bindings(
                _output(built, request, work_context, ()), request, consume=work_context.work.consume
            )
            work_context.work.consume(
                1 + len(output.bindings) + len(output.form_bindings) + len(output.form_plans) + len(output.resources)
            )
            return replace(
                output,
                bindings=MappingProxyType(dict(output.bindings)),
                form_bindings=MappingProxyType(dict(output.form_bindings)),
                form_plans=MappingProxyType(dict(output.form_plans)),
                resources=MappingProxyType(dict(output.resources)),
            )

        try:
            result = search_composition(
                SemanticOccurrence(OccurrenceId(("root",)), graph, root),
                rules,
                context,
                finalize=finalize,
                selections=selections,
                policies=options.policies,
                partitions=options.partitions,
                partition_hint=partition_hint,
            )
        except JointExhaustedError as error:
            _logger.debug("Joint allocation exhausted without a certified layout: %s", error.diagnostics)
            raise
        if result.diagnostics.completion == SearchCompletion.EXHAUSTED:
            _logger.debug(
                "Joint allocation returned a certified layout with unproven optimality: %s", result.diagnostics
            )
        page = result.allocation.select(options.pages)
        planned = replace(
            page,
            allocation=result.allocation,
            report=replace(page.report, search=result.diagnostics),
            metrics=PlanMetrics(
                partition_hint=partition_hint is not None,
                work_used=result.diagnostics.work_used,
                generated_states=result.diagnostics.generated_states,
                target_evaluations=result.diagnostics.target_evaluations,
                certified_combinations=result.diagnostics.certified_combinations,
            ),
        )
        if cache is not None:
            cache.remember_partitions(
                cache_key, {key: region.cuts for key, region in result.allocation.regions.items()}
            )
        return RetainedComposition.prepare(document, request, options, planned).present(options=options)
    candidate = select_composition(
        SemanticOccurrence(OccurrenceId(("root",)), graph, root), rules, context, selections=selections
    )
    if isinstance(candidate, UnsupportedCapability):
        raise CompositionRejectedError((candidate,))
    pending = [candidate]
    occurrences: set[OccurrenceId] = set()
    events: list[PlanEvent] = []
    while pending:
        context.work.consume()
        part = pending.pop()
        occurrences.add(part.offer.occurrence.identity)
        events.append(PlanEvent("composition.selection", "/".join(part.offer.occurrence.identity.path), part.offer.key))
        pending.extend(reversed(part.children))
    if selections.keys() - occurrences:
        message = "composition selections contain an unknown occurrence"
        raise LayoutInvariantError(message)
    built = build_composition(candidate, rules, context)
    if not isinstance(built, BuiltComposition):
        raise CompositionRejectedError(built.violations)
    return _output(built, request, context, tuple(events))


def _output[RenderTargetT, AdapterT, BodyT: scene.Body, ResourceT, PreparationT](
    built: BuiltComposition[ResourceT, BodyT, PreparationT, object],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    context: PlanningContext,
    selection_events: tuple[PlanEvent, ...],
) -> PlanResult[BodyT]:
    context.work.consume(
        1 + len(built.evaluation.resources.uses) + len(built.evaluation.effects) + len(built.evaluation.text)
    )
    events = list(selection_events)
    if not isinstance(built.body, request.target.body_type):
        message = "composition rules constructed a body for another target"
        raise LayoutInvariantError(message)
    resources = {
        f"asset:{identity.key}" if identity.kind == "attachment" else identity.key: use.value
        for identity, use in built.evaluation.resources.uses.items()
    }
    if len(resources) != len(built.evaluation.resources.uses):
        message = "composition resources have colliding delivery keys"
        raise LayoutInvariantError(message)
    assets = tuple(
        scene.Asset(value.key, value.name, value.media_type) for value in resources.values() if isinstance(value, Asset)
    )
    output = scene.Scene(scene.Codec.protocol, request.target.id, request.target.version, built.body, assets)
    effects = built.evaluation.effects
    events.extend(
        PlanEvent("composition.effect", "/".join(effect.source.keys), type(effect).__name__, PlanSeverity.DEGRADATION)
        for effect in effects
    )
    return PlanResult(
        output,
        built.bindings,
        PlanReport(tuple(events), scene_fingerprint=sha256(scene.Codec.dumps(output).encode()).hexdigest()),
        form_bindings=built.forms,
        resources=resources,
        metrics=PlanMetrics(work_used=context.work.spent),
        cost=built.evaluation.cost,
        effects=effects,
    )


@dataclass(frozen=True, slots=True)
class FixedComposition[RenderTargetT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT]:
    """Use custom typed rules with the same checks and public result as built-in composition.

    Rules must measure the selected tree, prepare immutable output, and validate
    construction independently. Reservations and platform capacities remain mandatory.
    """

    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT]
    selections: Mapping[OccurrenceId, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "selections", MappingProxyType(dict(self.selections)))

    def plan[AdapterT](
        self, rendered: DocumentLike[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    ) -> PlanResult[BodyT]:
        return _plan(rendered, request, self.rules, self.selections)


@dataclass(frozen=True, slots=True)
class AutomaticComposition[RenderTargetT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT]:
    """Use custom rules with bounded joint search and immutable page selection."""

    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT]
    options: CompositionOptions = CompositionOptions(automatic=True)

    def __post_init__(self) -> None:
        if not self.options.automatic:
            message = "AutomaticComposition requires automatic options"
            raise ValueError(message)

    def plan[AdapterT](
        self, rendered: DocumentLike[RenderTargetT], request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    ) -> PlanResult[BodyT]:
        return _plan(rendered, request, self.rules, self.options.selections, self.options)


@dataclass(frozen=True, slots=True)
class RuleComposition[RenderTargetT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT](
    TargetComposition[RenderTargetT, BodyT]
):
    """Keep a dialect's payload, resource, and preparation types bound through planning."""

    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT]

    def plan[AdapterT](
        self,
        rendered: DocumentLike[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions,
        cache: PlanCache | None = None,
    ) -> PlanResult[BodyT]:
        return _plan(rendered, request, self.rules, options.selections, options, cache)


@dataclass(frozen=True, slots=True)
class CompositionPlan[RenderTargetT, AdapterT, BodyT: scene.Body]:
    """Prepared input and options for built-in fixed or automatic composition.

    Automatic composition prepares authoring input before search. A default call
    derives its search allowance from the input; explicit options supply one.
    """

    document: Document[RenderTargetT]
    request: PlanRequest[BodyT, RenderTargetT, AdapterT]
    options: CompositionOptions

    @classmethod
    def prepare(
        cls,
        rendered: DocumentLike[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        options: CompositionOptions | None,
    ) -> CompositionPlan[RenderTargetT, AdapterT, BodyT]:
        document = as_document(rendered)
        if options is None or options.automatic:
            prepared = prepare_composition(document, request)
            if prepared.limitation is not None:
                identity = OccurrenceId(("root",))
                raise JointUnsupportedError(
                    SearchDiagnostics(
                        SearchCompletion.UNSUPPORTED,
                        optimal=False,
                        work_used=0,
                        work_limit=options.work_limit if options is not None else request.search_budget,
                        violations=(UnsupportedCapability(identity, prepared.limitation),),
                        unsupported=(identity,),
                    )
                )
            document = prepared.document
            if options is None:
                options = CompositionOptions(
                    automatic=True,
                    work_limit=(
                        max(CompositionOptions().work_limit, min(2_000_000, prepared.combinations * 200))
                        if request.search_budget == DEFAULT_SEARCH_BUDGET
                        else request.search_budget
                    ),
                )
        return cls(document, request, options)

    def execute(
        self,
        rendered: DocumentLike[RenderTargetT],
        *,
        cache: PlanCache | None = None,
        snapshot: PlanningSnapshot[BodyT] | None = None,
    ) -> PlanResult[BodyT]:
        request, options, document = self.request, self.options, self.document
        # Route factories can change IDs without changing their callable identity.
        if has_routed_page(rendered):
            cache = None
            snapshot = None
        key = request.exact_key()
        retained = request.presentation.retained_composition
        if (
            (snapshot is None or snapshot.enabled)
            and options.automatic
            and retained is not None
            and retained.matches(document, request, options)
        ):
            result = retained.reuse(document, request, options)
        else:
            effective = request.target.reserve(request.reservation)
            result = effective.dialect.composer(document, effective.limits).plan(
                document,
                request,
                options,
                cache if options.automatic and (snapshot is None or snapshot.enabled) else None,
            )
        if snapshot is not None:
            snapshot.stage(rendered, key, request.presentation, result)
        return result


def plan_composition[RenderTargetT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    options: CompositionOptions | None,
    *,
    cache: PlanCache | None = None,
    snapshot: PlanningSnapshot[BodyT] | None = None,
) -> PlanResult[BodyT]:
    """Resolve planning policy, then reuse or execute the target's bound rules."""
    if snapshot is not None:
        request = replace(request, fingerprints=snapshot.fingerprints if snapshot.enabled else None)
        # Routed inputs are never staged, so exact replay needs no tree traversal.
        exact = snapshot.replay(rendered, request.exact_key(), request.presentation)
        if exact is not None:
            return exact
    return CompositionPlan.prepare(rendered, request, options).execute(rendered, cache=cache, snapshot=snapshot)

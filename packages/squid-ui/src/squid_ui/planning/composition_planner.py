"""Public fixed and automatic composition through typed target rules."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from hashlib import sha256
from types import MappingProxyType
from typing import cast

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.document import DocumentLike, as_document
from squid_ui.errors import LayoutError, LayoutInvariantError, LayoutSearchExhaustedError
from squid_ui.html.target import HtmlLimits
from squid_ui.planning.allocation_policy import SearchCompletion, SearchDiagnostics
from squid_ui.planning.binding_preparation import prepare_bindings
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
from squid_ui.planning.composition_html import HtmlCompositionRules
from squid_ui.planning.composition_request import CompositionOptions
from squid_ui.planning.composition_slack import SlackCompositionRules
from squid_ui.planning.composition_v2 import V2CompositionRules
from squid_ui.planning.degradation import DegradationEffect, DegradationProfile
from squid_ui.planning.joint_search import JointExhaustedError, JointUnsupportedError, search_composition
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import select_composition
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanSeverity
from squid_ui.planning.semantics import UnsupportedSemanticError, semantic_graph
from squid_ui.semantic import Group
from squid_ui.slack.target import SlackMessageLimits

_logger = logging.getLogger(__name__)


class CompositionRejectedError(LayoutError):
    """The explicit candidate failed; other choices have not been searched."""

    def __init__(self, violations: tuple[CandidateViolation, ...]) -> None:
        self.violations = violations
        super().__init__(f"explicit composition rejected: {violations!r}")


def _context[BodyT: scene.Body, RenderTargetT, AdapterT](
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    work_limit: int | None = None,
) -> PlanningContext:
    return PlanningContext(
        WorkBudget(request.search_budget if work_limit is None else work_limit),
        request.localization,
        request.strict,
        request.palette,
        request.target.capabilities,
        request.target.reserve(request.reservation).capacities,
        request.chrome,
    )


def _plan[RenderTargetT, AdapterT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT],
    selections: Mapping[OccurrenceId, str],
    options: CompositionOptions | None = None,
) -> PlanResult[BodyT]:
    automatic = options is not None and options.automatic
    context = _context(request, options.work_limit if options is not None and automatic else None)
    document = as_document(rendered)
    if document.assets or request.positions or request.nav is not None:
        violation = UnsupportedCapability(
            OccurrenceId(("root",)),
            "composition does not support document assets or mounted pagination inputs; use composition.pages",
        )
        if automatic:
            raise JointUnsupportedError(
                SearchDiagnostics(
                    SearchCompletion.UNSUPPORTED,
                    optimal=False,
                    work_used=context.work.spent,
                    work_limit=context.work.limit,
                    violations=(violation,),
                    unsupported=(violation.occurrence,),
                )
            )
        raise CompositionRejectedError((violation,))
    try:
        graph = semantic_graph(Group(document.children, key=document.key or "@document"), consume=context.work.consume)
    except LayoutSearchExhaustedError as error:
        if not automatic:
            raise
        diagnostics = SearchDiagnostics(
            SearchCompletion.EXHAUSTED, optimal=False, work_used=context.work.spent, work_limit=context.work.limit
        )
        _logger.debug("Joint allocation exhausted during semantic traversal: %s", diagnostics)
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
            )
        except JointExhaustedError as error:
            _logger.debug("Joint allocation exhausted without a certified layout: %s", error.diagnostics)
            raise
        if result.diagnostics.completion == SearchCompletion.EXHAUSTED:
            _logger.debug(
                "Joint allocation returned a certified layout with unproven optimality: %s", result.diagnostics
            )
        page = result.allocation.select(options.pages)
        return replace(
            page,
            allocation=result.allocation,
            report=replace(page.report, search=result.diagnostics),
            metrics=PlanMetrics(
                work_used=result.diagnostics.work_used,
                generated_states=result.diagnostics.generated_states,
                target_evaluations=result.diagnostics.target_evaluations,
                certified_combinations=result.diagnostics.certified_combinations,
            ),
        )
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
    resources = {identity.key: use.value for identity, use in built.evaluation.resources.uses.items()}
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
        degradation=DegradationProfile.from_effects(
            [DegradationEffect(0, "/".join(effect.source.keys), semantic_steps=1) for effect in effects]
        ),
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


def plan_composition[RenderTargetT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    options: CompositionOptions,
) -> PlanResult[BodyT]:
    """Select the built-in rules without routing through a legacy target planner."""
    effective = request.target.reserve(request.reservation)
    # The public target couples limits and body types; narrow both before bridging
    # its generic parameter to one concrete built-in rules implementation.
    if isinstance(effective.limits, V2Limits) and effective.body_type is scene.ComponentsV2:
        v2_request = cast(PlanRequest[scene.ComponentsV2, RenderTargetT, AdapterT], request)
        result = _plan(
            rendered,
            v2_request,
            V2CompositionRules[RenderTargetT, object](effective.limits),
            options.selections,
            options,
        )
    elif isinstance(effective.limits, SlackMessageLimits) and effective.body_type is scene.SlackMessage:
        slack_request = cast(PlanRequest[scene.SlackMessage, RenderTargetT, AdapterT], request)
        result = _plan(
            rendered,
            slack_request,
            SlackCompositionRules[RenderTargetT, object](effective.limits),
            options.selections,
            options,
        )
    elif isinstance(effective.limits, HtmlLimits) and effective.body_type is scene.HtmlBody:
        html_request = cast(PlanRequest[scene.HtmlBody, RenderTargetT, AdapterT], request)
        result = _plan(
            rendered, html_request, HtmlCompositionRules[RenderTargetT, object](), options.selections, options
        )
    else:
        if options.automatic:
            raise JointUnsupportedError(
                SearchDiagnostics(
                    SearchCompletion.UNSUPPORTED,
                    optimal=False,
                    work_used=0,
                    work_limit=options.work_limit,
                    violations=(UnsupportedCapability(OccurrenceId(("root",)), "target composition rules"),),
                    unsupported=(OccurrenceId(("root",)),),
                )
            )
        raise CompositionRejectedError((UnsupportedCapability(OccurrenceId(("root",)), "target composition rules"),))
    return cast(PlanResult[BodyT], result)

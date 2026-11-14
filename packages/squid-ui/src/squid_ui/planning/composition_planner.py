"""Public fixed-composition planning, including typed custom target rules."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import cast

from squid_ui import scene
from squid_ui.assets import Asset
from squid_ui.document import DocumentLike, as_document
from squid_ui.errors import LayoutError, LayoutInvariantError
from squid_ui.html.target import HtmlLimits
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
from squid_ui.planning.limits import V2Limits
from squid_ui.planning.realizations import select_composition
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanEvent, PlanMetrics, PlanReport, PlanResult, PlanSeverity
from squid_ui.planning.semantics import UnsupportedSemanticError, semantic_graph
from squid_ui.semantic import Group
from squid_ui.slack.target import SlackMessageLimits


class CompositionRejectedError(LayoutError):
    """The explicit candidate failed; other choices have not been searched."""

    def __init__(self, violations: tuple[CandidateViolation, ...]) -> None:
        self.violations = violations
        super().__init__(f"explicit composition rejected: {violations!r}")


def _context[BodyT: scene.Body, RenderTargetT, AdapterT](
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
) -> PlanningContext:
    return PlanningContext(
        WorkBudget(request.search_budget),
        request.localization,
        request.strict,
        request.palette,
        request.target.capabilities,
        request.target.reserve(request.reservation).capacities,
    )


def _plan[RenderTargetT, AdapterT, BodyT: scene.Body, PayloadT, ResourceT, PreparationT](
    rendered: DocumentLike[RenderTargetT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    rules: TargetRules[RenderTargetT, object, PayloadT, ResourceT, BodyT, PreparationT],
    selections: Mapping[OccurrenceId, str],
) -> PlanResult[BodyT]:
    context = _context(request)
    document = as_document(rendered)
    if document.assets or request.positions or request.nav is not None:
        raise CompositionRejectedError(
            (
                UnsupportedCapability(
                    OccurrenceId(("root",)),
                    "fixed composition does not yet support document assets or pagination options",
                ),
            )
        )
    try:
        graph = semantic_graph(Group(document.children, key=document.key or "@document"), consume=context.work.consume)
    except UnsupportedSemanticError as error:
        raise CompositionRejectedError((UnsupportedCapability(OccurrenceId(("root",)), error.kind),)) from error
    root = next(ref for ref, unit in graph.units.items() if unit.parent is None)
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
        metrics=PlanMetrics(states_explored=context.work.spent),
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
            rendered, v2_request, V2CompositionRules[RenderTargetT, object](effective.limits), options.selections
        )
    elif isinstance(effective.limits, SlackMessageLimits) and effective.body_type is scene.SlackMessage:
        slack_request = cast(PlanRequest[scene.SlackMessage, RenderTargetT, AdapterT], request)
        result = _plan(
            rendered, slack_request, SlackCompositionRules[RenderTargetT, object](effective.limits), options.selections
        )
    elif isinstance(effective.limits, HtmlLimits) and effective.body_type is scene.HtmlBody:
        html_request = cast(PlanRequest[scene.HtmlBody, RenderTargetT, AdapterT], request)
        result = _plan(rendered, html_request, HtmlCompositionRules[RenderTargetT, object](), options.selections)
    else:
        raise CompositionRejectedError((UnsupportedCapability(OccurrenceId(("root",)), "target composition rules"),))
    return cast(PlanResult[BodyT], result)

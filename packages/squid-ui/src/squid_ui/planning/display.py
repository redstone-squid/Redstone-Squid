"""Reusable display data and owner-local values addressed by page slots."""

from collections.abc import Mapping
from dataclasses import dataclass

from squid_ui import scene
from squid_ui.authoring.presentations import FidelityEffect
from squid_ui.interactions import ActionBinding
from squid_ui.interactions.forms import FormBinding
from squid_ui.planning.resources import ResourceCost
from squid_ui.planning.result import PlanMetrics, PlanReport, PlanResult


@dataclass(frozen=True, slots=True)
class DisplayPage[BodyT: scene.Body]:
    """Certified output without callbacks, resource providers, or presentation writes."""

    scene: scene.Scene[BodyT]
    report: PlanReport
    metrics: PlanMetrics
    cost: ResourceCost
    effects: tuple[FidelityEffect, ...]

    @classmethod
    def from_result(cls, result: PlanResult[BodyT]) -> DisplayPage[BodyT]:
        return cls(result.scene, result.report, result.metrics, result.cost, result.effects)

    def bind(self, actions: Mapping[str, ActionBinding], live: PageBindings) -> PlanResult[BodyT]:
        return PlanResult(
            self.scene,
            actions,
            self.report,
            form_bindings=live.forms,
            form_plans=live.form_plans,
            resources=live.resources,
            metrics=self.metrics,
            cost=self.cost,
            effects=self.effects,
        )


@dataclass(frozen=True, slots=True)
class PageBindings:
    """Opaque live values for one certified page; never stored in its display template."""

    actions: Mapping[str, ActionBinding]
    forms: Mapping[str, FormBinding]
    form_plans: Mapping[str, object]
    resources: Mapping[str, object]

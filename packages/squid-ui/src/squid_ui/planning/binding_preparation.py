"""Prepare frontend handlers and forms without registering or publishing them."""

from collections.abc import Callable
from dataclasses import replace

from squid_ui import scene
from squid_ui.handlers import bind_handlers
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanResult


def prepare_bindings[BodyT: scene.Body, RenderTargetT, AdapterT](
    result: PlanResult[BodyT],
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    *,
    consume: Callable[[int], None] | None = None,
) -> PlanResult[BodyT]:
    if consume is not None:
        consume(1 + len(result.bindings) + len(result.form_bindings))
    frontend = request.target.adapter.frontend or request.target.adapter.name
    bindings, forms = bind_handlers(result.bindings, result.form_bindings, frontend)
    result = (
        replace(result, bindings=bindings, form_bindings=forms)
        if bindings is not result.bindings or forms is not result.form_bindings
        else result
    )
    if (prepare_form := request.target.adapter.form_planner) is not None and forms:
        result = replace(
            result,
            form_plans={
                key: prepare_form(
                    binding.spec,
                    target=request.target,
                    localization=request.localization,
                    strict=request.strict or request.composition is not None,
                )
                for key, binding in forms.items()
            },
        )
    return result

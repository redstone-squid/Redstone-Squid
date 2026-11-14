"""Public target-neutral planning dispatcher."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike, PortableDocumentLike, as_document
from squid_ui.handlers import bind_handlers
from squid_ui.pages import PagePosition
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.composition_planner import plan_composition
from squid_ui.planning.composition_request import CompositionOptions, CompositionPlanner
from squid_ui.planning.identity import has_routed_page
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.regions import contains_regions, plan_regions
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanResult
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.text import NEUTRAL, Localization


def plan[RenderTargetT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT] | PortableDocumentLike,
    *,
    target: Target[Any, BodyT, RenderTargetT, AdapterT],
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_COST,
    positions: Mapping[str, PagePosition] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationState | None = None,
    cache: PlanCache[BodyT] | None = None,
    memo: PlanMemo[BodyT] | None = None,
    composition: CompositionOptions | CompositionPlanner[RenderTargetT, BodyT] | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> PlanResult[BodyT]:
    """Choose a target layout and return its Scene, handlers, and planning diagnostics.

    The target's planner selects representations for the authored nodes and checks
    their resource limits. The returned PlanResult keeps the serializable display
    separate from Python handlers and file sources needed by the frontend.

    Planning reads presentation state without changing it. After successful delivery,
    the frontend must apply the result's session_updates so remembered pages and
    selections match the display. Reservations withhold space needed by other content.

    `composition=CompositionOptions(...)` opts into fixed choices through the shared
    core. A failed choice raises CompositionRejectedError without claiming other
    representations are infeasible. This path does not paginate or use caches.

    The backend controls fitting and supported adaptations. Discord's strict check
    rejects lossy measurement diagnostics; HTML and Slack reject degradation events.

    `cache` reuses callback-free structure; `memo` retains an owner's live result.
    The evaluation budget bounds Discord layout search; other backends compile
    their supported representations directly.
    """
    request = PlanRequest(
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
        positions=positions,
        nav=nav,
        session=session,
        search_budget=search_budget,
        composition=composition,
    )
    return plan_request(rendered, request, cache=cache, memo=memo)


def plan_request[RenderTargetT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[RenderTargetT] | PortableDocumentLike,
    request: PlanRequest[BodyT, RenderTargetT, AdapterT],
    *,
    cache: PlanCache[BodyT] | None = None,
    memo: PlanMemo[BodyT] | None = None,
) -> PlanResult[BodyT]:
    """Compile an existing request and select callbacks for the adapter's frontend."""
    if isinstance(request.composition, CompositionOptions):
        result = plan_composition(rendered, request, request.composition)
    elif request.composition is not None:
        result = request.composition.plan(cast(DocumentLike[RenderTargetT], rendered), request)
    else:
        # Routed factories must not replay stale IDs from structural caches.
        if has_routed_page(rendered):
            cache = None
            memo = None
        backend = request.target.dialect.planner
        document = as_document(rendered)
        result = (
            plan_regions(document, request)
            if contains_regions(document.children)
            else backend.plan(rendered, request, cache=cache, memo=memo)
        )
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


__all__ = ["plan", "plan_request"]

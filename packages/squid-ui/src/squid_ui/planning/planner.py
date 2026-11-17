"""Public target-neutral planning dispatcher."""

from collections.abc import Mapping
from typing import Any, cast

from squid_ui import scene
from squid_ui.authoring.document import DocumentLike, PortableDocumentLike
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.content.text import NEUTRAL, Localization
from squid_ui.pages import PagePosition
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.binding_preparation import prepare_bindings
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.composition_planner import plan_composition
from squid_ui.planning.composition_request import CompositionOptions, CompositionPlanner
from squid_ui.planning.pagination.navigation import PlannedNav
from squid_ui.planning.request import DEFAULT_SEARCH_BUDGET, PlanRequest
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanResult
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState


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
    cache: PlanCache | None = None,
    memo: PlanMemo[BodyT] | None = None,
    composition: CompositionOptions | CompositionPlanner[RenderTargetT, BodyT] | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> PlanResult[BodyT]:
    """Choose a target layout and return its Scene, handlers, and planning diagnostics.

    Shared composition selects representations for the authored nodes and checks
    their resource limits. The returned PlanResult keeps the serializable display
    separate from Python handlers and file sources needed by the frontend.

    Planning reads presentation state without changing it. After successful delivery,
    the frontend must apply the result's session_updates so remembered pages and
    selections match the display. Reservations withhold space needed by other content.

    All built-in targets use shared composition and allocation search.
    `composition=CompositionOptions(...)` configures the shared core, with
    optional fixed choices or automatic search. A rejected fixed choice does not
    claim that other representations are infeasible.

    Target rules define supported representations and resource limits. Strict mode
    rejects declared losses, including shortening and omission.

    `cache` retains callback-free partition hints, which require current certification.
    `memo` retains one owner's exact live result. Committed allocations support page
    turns without repeating search. An explicit search_budget bounds shared operations.
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
    cache: PlanCache | None = None,
    memo: PlanMemo[BodyT] | None = None,
) -> PlanResult[BodyT]:
    """Compile an existing request and select callbacks for the adapter's frontend."""
    if request.composition is None or isinstance(request.composition, CompositionOptions):
        result = plan_composition(
            cast(DocumentLike[RenderTargetT], rendered), request, request.composition, cache=cache, memo=memo
        )
    else:
        result = request.composition.plan(cast(DocumentLike[RenderTargetT], rendered), request)
    # Automatic composition prepares every reachable output inside its work
    # allowance. Cursor selection must not leave other pages with raw handlers.
    return result if result.allocation is not None else prepare_bindings(result, request)


__all__ = ["plan", "plan_request"]

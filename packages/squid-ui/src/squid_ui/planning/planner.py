"""Public target-neutral planning dispatcher."""

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, cast

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike, PortableDocumentLike, as_document
from squid_ui.pages import PagePosition
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.allocation_policy import SearchCompletion, SearchDiagnostics
from squid_ui.planning.binding_preparation import prepare_bindings
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.composition import OccurrenceId, UnsupportedCapability
from squid_ui.planning.composition_planner import plan_composition
from squid_ui.planning.composition_request import CompositionOptions, CompositionPlanner
from squid_ui.planning.identity import has_routed_page
from squid_ui.planning.joint_search import JointUnsupportedError
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.pagination_migration import prepare_pagination
from squid_ui.planning.region_detection import contains_regions
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.result import PlanResult
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState, RetainedCompositionUpdate
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

    Supported layout pagination uses shared allocation search. Other capabilities
    use the legacy target planner; report.legacy_pagination names the limitation.
    A failed or exhausted shared search never retries through that legacy planner.

    `composition=CompositionOptions(...)` selects the shared core explicitly, with
    optional fixed choices or automatic search. A rejected fixed choice does not
    claim that other representations are infeasible.

    The backend controls fitting and supported adaptations. Discord's strict check
    rejects lossy measurement diagnostics; HTML and Slack reject degradation events.

    `cache` reuses callback-free structure; `memo` retains an owner's live result.
    Shared pagination retains one mounted owner's committed allocation for page
    turns. Shared structural caches retain partition hints, recertifying current output. An explicit
    search_budget bounds shared operations, or legacy Discord layout evaluations.
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
        result = plan_composition(rendered, request, request.composition, cache=cache)
    elif request.composition is not None:
        result = request.composition.plan(cast(DocumentLike[RenderTargetT], rendered), request)
    else:
        # Routed factories must not replay stale IDs from structural caches.
        if has_routed_page(rendered):
            cache = None
            memo = None
        backend = request.target.dialect.planner
        document = as_document(rendered)
        migration = prepare_pagination(document, request)
        if migration.enabled:
            # The default adopts shared operation units; explicit limits remain bounded.
            options = CompositionOptions(
                automatic=True,
                work_limit=(
                    max(CompositionOptions().work_limit, min(2_000_000, migration.combinations * 200))
                    if request.search_budget == DEFAULT_SEARCH_BUDGET
                    else request.search_budget
                ),
            )
            result = plan_composition(
                migration.document, replace(request, shared_pagination=True), options, cache=cache
            )
        else:
            if contains_regions(document.children):
                identity = OccurrenceId(("root",))
                raise JointUnsupportedError(
                    SearchDiagnostics(
                        SearchCompletion.UNSUPPORTED,
                        optimal=False,
                        work_used=0,
                        work_limit=request.search_budget,
                        violations=(UnsupportedCapability(identity, migration.limitation or "region vocabulary"),),
                        unsupported=(identity,),
                    )
                )
            result = backend.plan(rendered, request, cache=cache, memo=memo)
            result = replace(result, session_updates=(*result.session_updates, RetainedCompositionUpdate(None)))
            if migration.limitation is not None:
                result = replace(
                    result,
                    report=replace(
                        result.report,
                        legacy_pagination=migration.limitation,
                    ),
                )
    # Automatic composition prepares every reachable output inside its work
    # allowance. Cursor selection must not leave other pages with raw handlers.
    return result if result.allocation is not None else prepare_bindings(result, request)


__all__ = ["plan", "plan_request"]

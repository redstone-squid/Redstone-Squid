"""Public target-neutral planning dispatcher."""

from collections.abc import Mapping
from typing import Any

from squid_ui import scene
from squid_ui.chrome import DEFAULT_CHROME, Chrome
from squid_ui.document import DocumentLike, PortableDocumentLike
from squid_ui.palette import DEFAULT_PALETTE, Palette
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.navigation import PlannedNav
from squid_ui.planning.resources import EMPTY_COST, ResourceCost
from squid_ui.planning.search import DEFAULT_SEARCH_BUDGET
from squid_ui.planning.target import Target
from squid_ui.runtime.presentation_state import PresentationState
from squid_ui.scene.model import PlanResult
from squid_ui.sources import Position
from squid_ui.text import NEUTRAL, Localization

EMPTY_RESERVATION = EMPTY_COST


def plan[ModeT, AdapterT, BodyT: scene.Body](
    rendered: DocumentLike[ModeT] | PortableDocumentLike,
    *,
    target: Target[Any, BodyT, ModeT, AdapterT],
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    positions: Mapping[str, Position] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationState | None = None,
    cache: PlanCache | None = None,
    memo: PlanMemo | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
) -> PlanResult[BodyT]:
    """Resolve a logical document through the selected target's planner backend."""
    if search_budget < 1:
        message = "planner search budget must be positive"
        raise ValueError(message)
    backend: Any = target.dialect.planner
    return backend.plan(
        rendered,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
        positions=positions,
        nav=nav,
        session=session,
        cache=cache,
        memo=memo,
        search_budget=search_budget,
    )


__all__ = ["EMPTY_RESERVATION", "plan"]

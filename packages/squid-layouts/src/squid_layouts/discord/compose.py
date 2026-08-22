"""Discord composition convenience built on the portable plan/draw seam."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

import discord

from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.discord.attachments import attachment_assets
from squid_layouts.discord.presentation import DiscordPresentation
from squid_layouts.discord.renderer import Renderer, Wire
from squid_layouts.discord.target import Target
from squid_layouts.document import DocumentLike
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.limits import LIMITS, V2Limits
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.planner import plan as plan_document
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET
from squid_layouts.planning.target import ResourceCost
from squid_layouts.profiling import OperationRecorder, SpanRecorder
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.scene.model import PlanResult
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization

logger = logging.getLogger(__name__)


@contextmanager
def _span(profile: OperationRecorder | None, name: str) -> Iterator[SpanRecorder | None]:
    if profile is None:
        yield None
        return
    with profile.span(name) as span:
        yield span


@dataclass(slots=True)
class Composition:
    """A resolved plan beside the complete Discord message it draws to."""

    presentation: DiscordPresentation
    plan: PlanResult

    @property
    def view(self) -> discord.ui.LayoutView:
        """The drawn view. Composition is Components V2, so there is always one."""
        return self.presentation.layout

    @property
    def assets(self) -> tuple[Asset, ...]:
        """Declarative files this composition expects to be uploaded with it."""
        return self.presentation.assets

    def files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; a sent `discord.File` cannot be re-sent."""
        return self.presentation.files()

    @property
    def page(self) -> int:
        return self.plan.scene.pagers[0].page if self.plan.scene.pagers else 0

    @property
    def pages(self) -> int:
        return self.plan.scene.pagers[0].pages if self.plan.scene.pagers else 1


def compose(
    rendered: DocumentLike,
    *,
    wire: Wire | None = None,
    renderer: Renderer | None = None,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
    positions: Mapping[str, Position] | None = None,
    nav: PlannedNav | None = None,
    session: PresentationSession | None = None,
    cache: PlanCache | None = None,
    search_budget: int = DEFAULT_SEARCH_BUDGET,
    profile: OperationRecorder | None = None,
) -> Composition:
    """Plan a logical document, then draw its resolved Discord scene."""
    with _span(profile, "planner") as planner_span:
        result = plan_document(
            rendered,
            target=Target(limits),
            chrome=chrome,
            localization=localization,
            palette=palette,
            strict=strict,
            reservation=reservation,
            positions=positions,
            nav=nav,
            session=session,
            cache=cache,
            search_budget=search_budget,
        )
        if planner_span is not None:
            planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
            planner_span.set_attribute("states_explored", result.metrics.states_explored)
            planner_span.set_attribute("search_fallback", result.metrics.search_fallback)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.search_fallbacks", int(result.metrics.search_fallback))
            profile.increment("planner.states_explored", result.metrics.states_explored)
    drawer = renderer if renderer is not None else Renderer(limits=limits)
    with _span(profile, "renderer"):
        view = drawer.draw(result.scene, plan=result, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return Composition(DiscordPresentation.components_v2(view, assets=attachment_assets(result)), result)


def render_static(
    nodes: DocumentLike,
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> discord.ui.LayoutView:
    """Plan and draw a sessionless Discord document."""
    return compose(
        nodes,
        limits=limits,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
    ).view

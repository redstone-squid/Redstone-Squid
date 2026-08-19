"""Discord composition convenience built on the portable plan/draw seam."""

import logging
from dataclasses import dataclass

import discord

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.discord.renderer import DiscordRenderer, Wire
from squid_layouts.discord.target import DiscordV2Target
from squid_layouts.document import DocumentLike
from squid_layouts.limits import LIMITS, V2Limits
from squid_layouts.planner import plan as plan_document
from squid_layouts.presentation import PresentationSession
from squid_layouts.scene import PlanResult
from squid_layouts.solve import PageNav, PageState
from squid_layouts.target import ResourceCost

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Composition:
    """A resolved plan beside its mechanically drawn Discord view."""

    view: discord.ui.LayoutView
    plan: PlanResult

    @property
    def page(self) -> int:
        return self.plan.scene.pagers[0].page if self.plan.scene.pagers else 0

    @property
    def pages(self) -> int:
        return self.plan.scene.pagers[0].pages if self.plan.scene.pagers else 1

    @property
    def interventions(self) -> list[str]:
        """Drawing no longer clamps; retained as an empty audit result."""
        return []


def compose(
    rendered: DocumentLike,
    *,
    into: discord.ui.LayoutView | None = None,
    wire: Wire | None = None,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reserved_text: int = 0,
    page: PageState = None,
    nav: PageNav | None = None,
    session: PresentationSession | None = None,
) -> Composition:
    """Plan a logical document, then draw its resolved Discord scene."""
    result = plan_document(
        rendered,
        target=DiscordV2Target(limits),
        chrome=chrome,
        strict=strict,
        reservation=ResourceCost({"display_text": reserved_text}),
        page=page,
        nav=nav,
        session=session,
    )
    view = DiscordRenderer(limits=limits).draw(result.scene, plan=result, into=into, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return Composition(view=view, plan=result)


def render_static(
    nodes: DocumentLike,
    *,
    limits: V2Limits = LIMITS,
    chrome: Chrome = DEFAULT_CHROME,
    strict: bool = False,
    reserved_text: int = 0,
) -> discord.ui.LayoutView:
    """Plan and draw a sessionless Discord document."""
    return compose(nodes, limits=limits, chrome=chrome, strict=strict, reserved_text=reserved_text).view

"""Composing classic Discord messages: `sl.discord.classic.compose` and friends.

A separate module rather than a mode flag on `sl.discord.compose`, because the author picks
the message mode and should have to say so. The two produce different messages with different
capabilities, and a default that silently decides which one you get is the thing this whole
target exists to avoid.
"""

import logging
from collections.abc import Mapping

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.discord.classic_renderer import ClassicRenderer, Wire
from squid_layouts.discord.presentation import DiscordModeError, DiscordPresentation
from squid_layouts.discord.target import CLASSIC_TARGET, Target
from squid_layouts.document import DocumentLike
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.limits import CLASSIC_LIMITS, ClassicLimits
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.planner import plan as plan_document
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET
from squid_layouts.planning.target import ResourceCost
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.sources import Position
from squid_layouts.text import NEUTRAL, Localization

logger = logging.getLogger(__name__)


def _classic_limits(target: Target) -> ClassicLimits:
    if not isinstance(target.limits, ClassicLimits):
        message = f"sl.discord.classic plans classic messages; {target.id!r} is not a classic target"
        raise DiscordModeError(message)
    return target.limits


def compose(
    rendered: DocumentLike,
    *,
    wire: Wire | None = None,
    renderer: ClassicRenderer | None = None,
    target: Target = CLASSIC_TARGET,
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
):
    """Plan a logical document, then draw the complete classic message it resolves to."""
    from squid_layouts.discord.compose import Composition

    result = plan_document(
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
        search_budget=search_budget,
    )
    drawer = renderer if renderer is not None else ClassicRenderer(limits=_classic_limits(target))
    presentation = drawer.draw(result.scene, plan=result, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return Composition(presentation, result)


def render_static(
    nodes: DocumentLike,
    *,
    target: Target = CLASSIC_TARGET,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> DiscordPresentation:
    """Plan and draw a sessionless classic document as one complete message.

    A presentation, never a bare view: the embeds *are* the message here, and handing back
    only the controls would leave the caller to reassemble the half that carries the content.
    """
    return compose(
        nodes,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
    ).presentation


__all__ = ["CLASSIC_LIMITS", "ClassicRenderer", "compose", "render_static"]

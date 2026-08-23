"""Discord composition convenience built on the portable plan/draw seam."""

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

import discord

from squid_layouts.assets import Asset
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.discord.adapter import require_discord_py_target
from squid_layouts.discord.presentation import DiscordModeError, DiscordPresentation
from squid_layouts.discord.renderer import V2Renderer, Wire
from squid_layouts.discord.target import V2_TARGET, Target
from squid_layouts.document import DocumentLike
from squid_layouts.palette import DEFAULT_PALETTE, Palette
from squid_layouts.planning.adapter import ADAPTER_RENDER_V2
from squid_layouts.planning.cache import PlanCache
from squid_layouts.planning.limits import V2Limits
from squid_layouts.planning.navigation import PlannedNav
from squid_layouts.planning.planner import EMPTY_RESERVATION
from squid_layouts.planning.planner import plan as plan_document
from squid_layouts.planning.search import DEFAULT_SEARCH_BUDGET
from squid_layouts.planning.target import ResourceCost
from squid_layouts.profiling import OperationRecorder, SpanRecorder
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.presentation import PresentationSession
from squid_layouts.scene.model import PlanResult, SceneComponentsV2
from squid_layouts.sources import Position
from squid_layouts.target_types import ComponentsV2Target, DiscordPyAdapter
from squid_layouts.text import NEUTRAL, Localization

logger = logging.getLogger(__name__)


def _v2_limits(target: Target) -> V2Limits:
    """A Components V2 target's limits. Anything else does not belong in this module."""
    if not isinstance(target.limits, V2Limits):
        message = f"sl.discord.compose plans Components V2; {target.id!r} is not a V2 target"
        raise DiscordModeError(message)
    return target.limits


@contextmanager
def _span(profile: OperationRecorder | None, name: str) -> Iterator[SpanRecorder | None]:
    if profile is None:
        yield None
        return
    with profile.span(name) as span:
        yield span


@dataclass(slots=True)
class Composition[ViewT: (discord.ui.LayoutView, discord.ui.View | None), BodyT = SceneComponentsV2]:
    """A resolved plan beside the complete Discord message it draws to.

    Generic over the view because the two modes differ in what they promise. A Components V2
    composition always has a `LayoutView` — it *is* the message. A classic composition has a
    `View` only when the document produced controls, and its embeds carry the rest.
    """

    presentation: DiscordPresentation
    plan: PlanResult[BodyT]

    @property
    def view(self) -> ViewT:
        """The drawn view, typed by which mode this composition is for."""
        return cast(ViewT, self.presentation.view)

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
    renderer: V2Renderer | None = None,
    target: Target[ComponentsV2Target, DiscordPyAdapter, SceneComponentsV2] = V2_TARGET,
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
) -> Composition[discord.ui.LayoutView, SceneComponentsV2]:
    """Plan a logical document, then draw its resolved Components V2 scene."""
    adapter = require_discord_py_target(target, ADAPTER_RENDER_V2, "compose Components V2")
    with _span(profile, "planner") as planner_span:
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
        if planner_span is not None:
            planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
            planner_span.set_attribute("states_explored", result.metrics.states_explored)
            planner_span.set_attribute("search_fallback", result.metrics.search_fallback)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.search_fallbacks", int(result.metrics.search_fallback))
            profile.increment("planner.states_explored", result.metrics.states_explored)
    drawer = renderer if renderer is not None else V2Renderer(limits=_v2_limits(target), adapter=adapter)
    with _span(profile, "renderer"):
        presentation = drawer.draw(result.scene, plan=result, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return Composition(presentation, result)


def render_static(
    nodes: DocumentLike | Component,
    *,
    target: Target[ComponentsV2Target, DiscordPyAdapter, SceneComponentsV2] = V2_TARGET,
    chrome: Chrome = DEFAULT_CHROME,
    localization: Localization = NEUTRAL,
    palette: Palette = DEFAULT_PALETTE,
    strict: bool = False,
    reservation: ResourceCost = EMPTY_RESERVATION,
) -> DiscordPresentation:
    """Plan and draw a sessionless Components V2 document as a complete message."""
    return compose(
        nodes.render() if isinstance(nodes, Component) else nodes,
        target=target,
        chrome=chrome,
        localization=localization,
        palette=palette,
        strict=strict,
        reservation=reservation,
    ).presentation

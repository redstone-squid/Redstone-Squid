"""Shared planning, profiling, drawing, and degradation logging for Discord messages.

Classic and Components V2 entry points choose their own target and renderer, then
use this pipeline to produce a payload alongside its bindings and plan report.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, cast, override

import discord
from squid_ui.authoring.document import DocumentLike
from squid_ui.content.assets import Asset
from squid_ui.ext.discord import scene
from squid_ui.ext.discord.messages.payload import MessagePayload
from squid_ui.ext.discord.rendering.controls import Wire
from squid_ui.ext.discord.target_types import DiscordPyAdapter
from squid_ui.planning.cache import PlanCache
from squid_ui.planning.planner import plan_request
from squid_ui.planning.request import PlanRequest
from squid_ui.planning.result import PlanResult, PlanSeverity
from squid_ui.planning.snapshot import PlanningSnapshot
from squid_ui.profiling import OperationRecorder, SpanRecorder
from squid_ui.renderer import Renderer

logger = logging.getLogger(__name__)


class MountedRenderer[BodyT: scene.Body](Renderer[BodyT, MessagePayload], Protocol):
    """A message renderer that accepts a mount's control-wiring callback.

    wire builds SDK items bound to the current mount generation using the matching
    plan's bindings. Both classic and Components V2 renderers implement this contract.
    """

    @override
    def draw(
        self,
        document: scene.Scene[BodyT],
        *,
        plan: PlanResult[BodyT] | None = None,
        wire: Wire | None = None,
    ) -> MessagePayload: ...


@dataclass(slots=True)
class RenderedMessage[ViewT: (discord.ui.LayoutView, discord.ui.View | None), BodyT = scene.ComponentsV2]:
    """A resolved plan beside the complete Discord message it draws to.

    Generic over the view because the two modes differ in what they promise. A Components V2
    composition always has a `LayoutView` — it *is* the message. A classic composition has a
    `View` only when the document produced controls, and its embeds carry the rest.
    """

    payload: MessagePayload
    plan: PlanResult[BodyT]

    @property
    def view(self) -> ViewT:
        """The drawn view, typed by which message mode this render is for."""
        return cast(ViewT, self.payload.view)

    @property
    def assets(self) -> tuple[Asset, ...]:
        """Declarative files this rendered message expects to upload."""
        return self.payload.assets

    def build_files(self) -> list[discord.File]:
        """Materialize fresh file wrappers; a sent `discord.File` cannot be re-sent."""
        return self.payload.build_files()

    @property
    def page(self) -> int:
        """Zero-based active page, or zero when the scene is not paginated."""
        return self.plan.scene.pagers[0].page if self.plan.scene.pagers else 0

    @property
    def pages(self) -> int | None:
        """Exact total when known; None for a partial plan, one without a pager."""
        return self.plan.scene.pagers[0].pages if self.plan.scene.pagers else 1


@contextmanager
def span(profile: OperationRecorder | None, name: str) -> Iterator[SpanRecorder | None]:
    """Record one phase when profiling is enabled; otherwise yield None."""
    if profile is None:
        yield None
        return
    with profile.span(name) as span_recorder:
        yield span_recorder


def plan_and_draw[BodyT: scene.Body, RenderTargetT](
    rendered: DocumentLike[RenderTargetT, Any],
    request: PlanRequest[BodyT, RenderTargetT, DiscordPyAdapter],
    *,
    drawer: MountedRenderer[BodyT],
    wire: Wire | None = None,
    cache: PlanCache | None = None,
    snapshot: PlanningSnapshot[BodyT] | None = None,
    profile: OperationRecorder | None = None,
) -> tuple[MessagePayload, PlanResult[BodyT]]:
    """Resolve a document for its target and draw the message its scene describes.

    The result travels back beside the payload because the plan holds what the payload
    cannot: the callbacks, the degradation report, and the session updates a mount stages.
    """
    with span(profile, "planner") as planner_span:
        result = plan_request(rendered, request, cache=cache, snapshot=snapshot)
        if planner_span is not None:
            planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
            planner_span.set_attribute("reuse", result.metrics.reuse.value)
            planner_span.set_attribute("work_used", result.metrics.work_used)
            planner_span.set_attribute("target_evaluations", result.metrics.target_evaluations)
            planner_span.set_attribute("generated_states", result.metrics.generated_states)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.work_used", result.metrics.work_used)
            profile.increment("planner.target_evaluations", result.metrics.target_evaluations)
            profile.increment("planner.generated_states", result.metrics.generated_states)
    with span(profile, "renderer"):
        payload = drawer.draw(result.scene, plan=result, wire=wire)
    warnings = tuple(event for event in result.report.events if event.severity != PlanSeverity.ADAPTATION)
    if warnings:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in warnings))
    return payload, result


__all__ = ["plan_and_draw", "span"]

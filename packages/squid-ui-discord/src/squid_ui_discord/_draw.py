"""Shared planning, profiling, drawing, and degradation logging for Discord messages.

Classic and Components V2 entry points choose their own target and renderer, then
use this pipeline to produce a payload alongside its bindings and plan report.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from squid_ui import scene
from squid_ui.document import DocumentLike
from squid_ui.planning.cache import PlanCache, PlanMemo
from squid_ui.planning.planner import plan_request
from squid_ui.planning.request import PlanRequest
from squid_ui.profiling import OperationRecorder, SpanRecorder
from squid_ui.scene.model import PlanResult
from squid_ui.target_types import DiscordPyAdapter
from squid_ui_discord.message_payload import MessagePayload
from squid_ui_discord.renderer import MountedRenderer, Wire

logger = logging.getLogger(__name__)


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
    cache: PlanCache[BodyT] | None = None,
    memo: PlanMemo[BodyT] | None = None,
    profile: OperationRecorder | None = None,
) -> tuple[MessagePayload, PlanResult[BodyT]]:
    """Resolve a document for its target and draw the message its scene describes.

    The result travels back beside the payload because the plan holds what the payload
    cannot: the callbacks, the degradation report, and the session updates a mount stages.
    """
    with span(profile, "planner") as planner_span:
        result = plan_request(rendered, request, cache=cache, memo=memo)
        if planner_span is not None:
            planner_span.set_attribute("cache_hit", result.metrics.cache_hit)
            planner_span.set_attribute("states_explored", result.metrics.states_explored)
            planner_span.set_attribute("search_fallback", result.metrics.search_fallback)
        if profile is not None:
            profile.increment("planner.calls")
            profile.increment("planner.cache_hits", int(result.metrics.cache_hit))
            profile.increment("planner.search_fallbacks", int(result.metrics.search_fallback))
            profile.increment("planner.states_explored", result.metrics.states_explored)
    with span(profile, "renderer"):
        payload = drawer.draw(result.scene, plan=result, wire=wire)
    if result.report.events:
        logger.warning("layout degraded: %s", "; ".join(event.message for event in result.report.events))
    return payload, result


__all__ = ["plan_and_draw", "span"]

"""The plan-then-draw pipeline both Discord dialects run.

`squid_ui_discord.render_message` and `squid_ui_discord.classic.render_message` are separate
entry points on purpose -- the author picks the message mode and should have to say so -- but
what they *do* between a document and a payload never differed: plan, record the planner's
metrics, draw, warn about degradation. That part lives here so the two stay one implementation
while remaining two APIs.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

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
    """Time one phase when a recorder is present, and cost nothing when it is not."""
    if profile is None:
        yield None
        return
    with profile.span(name) as span_recorder:
        yield span_recorder


def plan_and_draw[BodyT: scene.Body, RenderTargetT](
    rendered: DocumentLike[RenderTargetT],
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

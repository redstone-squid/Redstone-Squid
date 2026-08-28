"""Target-neutral planning backend and dialect contracts."""

from typing import TYPE_CHECKING, Any, Protocol

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.resources import TargetLimits

if TYPE_CHECKING:
    from squid_ui.document import DocumentLike
    from squid_ui.planning.cache import PlanCache, PlanMemo
    from squid_ui.planning.request import PlanRequest
    from squid_ui.scene.model import PlanResult


class TargetPlanner[LimitsT: TargetLimits, BodyT: scene.Body, RenderTargetT, AdapterT](Protocol):
    """A complete compiler backend for one family of target dialects."""

    def plan(
        self,
        rendered: DocumentLike[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        *,
        cache: PlanCache | None,
        memo: PlanMemo | None,
    ) -> PlanResult[BodyT]: ...


class TargetDialect[LimitsT: TargetLimits, BodyT: scene.Body, RenderTargetT](Protocol):
    """A target's identity, capabilities, limits, and complete planner backend."""

    id: str
    version: int
    capabilities: frozenset[Capability]
    render_target: type[RenderTargetT]
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool

    @property
    def planner(self) -> TargetPlanner[LimitsT, BodyT, RenderTargetT, Any]: ...


__all__ = ["TargetDialect", "TargetPlanner"]

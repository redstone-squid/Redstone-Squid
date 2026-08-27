"""Target-neutral planning backend and dialect contracts."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.resources import TargetLimits

if TYPE_CHECKING:
    from squid_ui.chrome import Chrome
    from squid_ui.document import DocumentLike
    from squid_ui.palette import Palette
    from squid_ui.planning.cache import PlanCache, PlanMemo
    from squid_ui.planning.navigation import PlannedNav
    from squid_ui.planning.resources import ResourceCost
    from squid_ui.planning.target import Target
    from squid_ui.runtime.presentation_state import PresentationState
    from squid_ui.scene.model import PlanResult
    from squid_ui.sources import Position
    from squid_ui.text import Localization


class TargetPlanner[LimitsT: TargetLimits, BodyT: scene.Body, ModeT, AdapterT](Protocol):
    """A complete compiler backend for one family of target dialects."""

    def plan(
        self,
        rendered: DocumentLike[ModeT],
        *,
        target: Target[LimitsT, BodyT, ModeT, AdapterT],
        chrome: Chrome,
        localization: Localization,
        palette: Palette,
        strict: bool,
        reservation: ResourceCost,
        positions: Mapping[str, Position] | None,
        nav: PlannedNav | None,
        session: PresentationState | None,
        cache: PlanCache | None,
        memo: PlanMemo | None,
        search_budget: int,
    ) -> PlanResult[BodyT]: ...


class TargetDialect[LimitsT: TargetLimits, BodyT: scene.Body, ModeT](Protocol):
    """A target's identity, capabilities, limits, and complete planner backend."""

    id: str
    version: int
    capabilities: frozenset[Capability]
    mode: type[ModeT]
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool

    @property
    def planner(self) -> TargetPlanner[LimitsT, BodyT, ModeT, Any]: ...


__all__ = ["TargetDialect", "TargetPlanner"]

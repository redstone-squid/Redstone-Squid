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
    """A complete compiler backend for one family of target dialects, reached through `TargetDialect.planner`."""

    def plan(
        self,
        rendered: DocumentLike[RenderTargetT],
        request: PlanRequest[BodyT, RenderTargetT, AdapterT],
        *,
        cache: PlanCache[BodyT] | None,
        memo: PlanMemo[BodyT] | None,
    ) -> PlanResult[BodyT]:
        """Compile one document into a scene of `request.target.dialect.body_type`.

        Consults `memo` for an exact hit and `cache` for a structural one before searching, and
        stores into both when given. Raises `LayoutInvariantError` for a document the target
        cannot express, `UnsolvableLayoutError` when no layout fits the budget, and
        `LayoutDegradedError` when `request.strict` and the chosen layout is not lossless.
        """
        ...


class TargetDialect[LimitsT: TargetLimits, BodyT: scene.Body, RenderTargetT](Protocol):
    """A target's identity, capabilities, limits, and complete planner backend.

    The first axis of a `Target`; `AdapterProfile` is the second. Discord dialects also satisfy
    `discord_dialect.DiscordDialect`, which adds the per-message methods the Discord planner calls.
    """

    id: str
    """Stable protocol name, such as `discord.components-v2`, recorded in every scene planned against it."""
    version: int
    """The scene's `target_version` and part of the target fingerprint; a renderer refuses one it cannot draw."""
    capabilities: frozenset[Capability]
    """What the protocol itself can draw; adapter behaviors and extension strings are never in here."""
    render_target: type[RenderTargetT]
    """The marker type that decides which nodes a document for this dialect may hold."""
    body_type: type[BodyT]
    """The `scene.Body` subclass `planner.plan` produces."""
    default_limits: LimitsT
    """The limits a target built without an explicit table gets."""
    realizes_extensions: bool
    """Whether an `Extension` node can lower to a native item here; when false it always takes its fallback."""

    @property
    def planner(self) -> TargetPlanner[LimitsT, BodyT, RenderTargetT, Any]:
        """The backend that compiles for this dialect; a property so the import can stay lazy."""
        ...


__all__ = ["TargetDialect", "TargetPlanner"]

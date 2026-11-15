"""Target-neutral planning backend and dialect contracts."""

from typing import Protocol

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.resources import TargetLimits


class TargetDialect[LimitsT: TargetLimits, BodyT: scene.Body, RenderTargetT](Protocol):
    """A target's identity, capabilities, limits, and scene contract."""

    id: str
    version: int
    capabilities: frozenset[Capability]
    render_target: type[RenderTargetT]
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool


__all__ = ["TargetDialect"]

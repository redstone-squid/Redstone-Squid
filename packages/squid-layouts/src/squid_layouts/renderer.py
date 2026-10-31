"""Mechanical renderer protocol for resolved scenes."""

from typing import Protocol

from squid_layouts.scene.model import PlanResult, SceneDocument


class Renderer[OutputT](Protocol):
    """Draw an already-planned scene without changing layout decisions."""

    def draw(self, scene: SceneDocument, *, plan: PlanResult | None = None) -> OutputT: ...

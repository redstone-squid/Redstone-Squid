"""Mechanical renderer protocol for resolved scenes."""

from typing import Protocol

from squid_layouts.scene.model import PlanResult, SceneBody, SceneDocument


class Renderer[BodyT: SceneBody, OutputT](Protocol):
    """Draw an already-planned scene without changing layout decisions.

    Parameterized by the body it draws, not only by what it returns. An unparameterized
    `SceneDocument` here made the protocol unsatisfiable: parameters are contravariant, so
    a renderer narrowing `draw` to its own body type — which every real one does — could
    not implement it, and the protocol sat declared and structurally dead.
    """

    def draw(self, scene: SceneDocument[BodyT], *, plan: PlanResult[BodyT] | None = None) -> OutputT: ...

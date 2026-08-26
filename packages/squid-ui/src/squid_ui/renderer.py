"""Mechanical renderer protocol for resolved scenes."""

from typing import Protocol

from squid_ui import scene
from squid_ui.scene.model import PlanResult


class Renderer[BodyT: scene.Body, OutputT](Protocol):
    """Draw an already-planned scene without changing layout decisions.

    Parameterized by the body it draws, not only by what it returns. An unparameterized
    `Scene` here made the protocol unsatisfiable: parameters are contravariant, so
    a renderer narrowing `draw` to its own body type — which every real one does — could
    not implement it, and the protocol sat declared and structurally dead.
    """

    def draw(self, document: scene.Scene[BodyT], *, plan: PlanResult[BodyT] | None = None) -> OutputT: ...

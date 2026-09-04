"""Mechanical renderer protocol for resolved scenes."""

from typing import Protocol

from squid_ui import scene
from squid_ui.scene.model import PlanResult


class Renderer[BodyT: scene.Body, OutputT](Protocol):
    """Draw an already-planned scene without changing layout decisions.

    Parameterized by the body it draws as well as by what it returns: parameters are
    contravariant, so a renderer that narrows `draw` to its own body type -- which every real
    one does -- can only satisfy a protocol that is itself narrowed to that body.
    """

    def draw(self, document: scene.Scene[BodyT], *, plan: PlanResult[BodyT] | None = None) -> OutputT:
        """Draw `document` to the frontend's native output.

        `plan` is the result that produced `document`, when the caller has it; a renderer reads
        attachments and cache identity from it and draws without them when it is `None`.
        """
        ...

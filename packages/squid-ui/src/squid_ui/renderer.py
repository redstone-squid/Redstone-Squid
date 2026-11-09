"""Mechanical renderer contracts for resolved scenes."""

from collections.abc import Callable
from typing import Protocol

from squid_ui import scene
from squid_ui.planning.result import PlanResult

type AssetResolver = Callable[[scene.Asset], str | None]
"""Resolve planned asset metadata to a frontend-consumable reference, if available."""


class Renderer[BodyT: scene.Body, OutputT](Protocol):
    """Draw a resolved scene without changing layout decisions.

    BodyT identifies the scene body this renderer accepts; OutputT identifies the
    frontend payload it produces. Plan resources carry runtime objects omitted from
    the serializable scene.
    """

    def draw(self, document: scene.Scene[BodyT], *, plan: PlanResult[BodyT] | None = None) -> OutputT: ...

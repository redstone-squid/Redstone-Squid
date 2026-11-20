"""Placement of a child component in an authored layout."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from squid_ui.target_types import Renderable, RenderTarget

if TYPE_CHECKING:
    from squid_ui.runtime.definition import Component


@dataclass(frozen=True, slots=True)
class Boundary[RenderTargetT = RenderTarget, ContextT = object](Renderable[RenderTargetT, ContextT]):
    """Place a child component in the layout under its own control and presentation keys.

    The runtime replaces this node with the child's rendered layout before planning.
    Component.keyed constructs it while preserving the child's target and context requirements.
    """

    component: Component[RenderTargetT, ContextT]
    key: str

    def __post_init__(self) -> None:
        if not self.key:
            message = "Boundary key must not be empty"
            raise ValueError(message)
        if "." in self.key:
            message = "Boundary key must not contain '.'"
            raise ValueError(message)

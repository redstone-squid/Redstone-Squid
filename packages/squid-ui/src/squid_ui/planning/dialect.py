"""Target-neutral planning backend and dialect contracts."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from squid_ui import scene
from squid_ui.capabilities import Capability
from squid_ui.planning.resources import TargetLimits

if TYPE_CHECKING:
    from squid_ui.authoring.document import DocumentLike
    from squid_ui.planning.composition_input import InputPreparation
    from squid_ui.planning.composition_request import TargetComposition
    from squid_ui.planning.pagination.navigation import PlannedNav
    from squid_ui.target_types import Renderable


class NativeInput(Protocol):
    """A target's native vocabulary visitor, scoped to one input preparation."""

    def visit(self, node: Renderable[Any]) -> Renderable[Any] | None: ...


@dataclass(frozen=True, slots=True)
class InputPolicy:
    """Vocabulary and sizing facts used before shared allocation search."""

    text_ladder: int = 1
    character_axis: bool = False
    native: Callable[[InputPreparation], NativeInput] | None = None
    budget_ladder: bool = False
    inspect: bool = True


class TargetDialect[LimitsT: TargetLimits, BodyT: scene.Body, RenderTargetT](Protocol):
    """A target's identity, capabilities, limits, and scene contract."""

    id: str
    version: int
    capabilities: frozenset[Capability]
    render_target: type[RenderTargetT]
    body_type: type[BodyT]
    default_limits: LimitsT
    realizes_extensions: bool

    @property
    def navigation(self) -> PlannedNav | None: ...

    def input_policy(self, limits: LimitsT) -> InputPolicy: ...

    def composer[R](self, document: DocumentLike[R], limits: LimitsT) -> TargetComposition[R, BodyT]: ...


__all__ = ["InputPolicy", "TargetDialect"]

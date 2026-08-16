"""Schematic request objects, built the same way by the bot, the API, and the worker."""

from dataclasses import dataclass
from typing import Literal

from squid.schematics.domain.models import SchematicFormat, Vector3


@dataclass(frozen=True, slots=True)
class IngestRequest:
    """A schematic upload awaiting analysis."""

    data: bytes
    filename: str
    uploaded_by_account_id: int | None = None
    with_lattice: bool = True


@dataclass(frozen=True, slots=True)
class ConvertRequest:
    """A format and/or data-version conversion of a stored schematic."""

    target_format: SchematicFormat
    target_data_version: int | None = None


MIN_RENDER_EXTENT = 64
MAX_RENDER_EXTENT = 4096
"""Pixel bounds on a rendered image, matching the deployment's configured render size.

Enforced in the request object rather than at each transport because the framing is now
caller-chosen: a render is sized in the worker's memory, so an unbounded extent is a way to
kill the engine from a Discord command or an unauthenticated GET.
"""


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Camera and framing for one headless render.

    The defaults produce a rotation-stable isometric view on a transparent background, which
    reads correctly against both Discord themes.
    """

    width: int = 768
    height: int = 768
    projection: Literal["orthographic", "perspective"] = "orthographic"
    sphere_fit: bool = True
    yaw: float | None = None
    pitch: float | None = None
    zoom: float | None = None
    background: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        for axis, extent in (("width", self.width), ("height", self.height)):
            if not MIN_RENDER_EXTENT <= extent <= MAX_RENDER_EXTENT:
                msg = f"Render {axis} must be between {MIN_RENDER_EXTENT} and {MAX_RENDER_EXTENT} pixels."
                raise ValueError(msg)
        if self.zoom is not None and self.zoom <= 0:
            msg = "Render zoom must be positive."
            raise ValueError(msg)
        if not all(0.0 <= channel <= 1.0 for channel in self.background):
            msg = "Render background channels must be between 0 and 1."
            raise ValueError(msg)

    def recipe_fields(self) -> tuple[object, ...]:
        """Return the values that identify this render for cache-key purposes."""
        return (
            self.width,
            self.height,
            self.projection,
            self.sphere_fit,
            self.yaw,
            self.pitch,
            self.zoom,
            self.background,
        )


@dataclass(frozen=True, slots=True)
class SimulationRequest:
    """A redstone simulation run.

    `input_position` is the block to right-click. It is never guessed: it comes from Insign
    sign annotations inside the schematic, from a lone unambiguous lever, or from an explicit
    coordinate supplied by a moderator.
    """

    input_position: Vector3 | None = None
    watch_positions: tuple[Vector3, ...] = ()
    max_ticks: int = 200

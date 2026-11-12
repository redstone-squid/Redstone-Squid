"""Validated recipes shared by video clients and native workers."""

from typing import Annotated, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from squid.schematics.domain.values import VerifiedResourcePack

type VideoCoordinate = Annotated[int, Field(strict=True, ge=-(2**31), le=2**31 - 1)]
type VideoPosition = tuple[VideoCoordinate, VideoCoordinate, VideoCoordinate]


class VideoInteraction(BaseModel):
    """Right-click a control before stepping the specified game tick."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tick: int = Field(ge=0, le=199, strict=True)
    position: VideoPosition | None = None


class VideoRenderRequest(BaseModel):
    """A bounded, real-time simulation clip with a constant-pitch camera orbit.

    An empty interaction list records the saved circuit without actuating it.
    A missing position asks the worker to resolve a single lever or button.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    simulation_enabled: bool = Field(default=True, strict=True)
    width: int = Field(default=640, ge=64, le=1280, multiple_of=2, strict=True)
    height: int = Field(default=360, ge=64, le=720, multiple_of=2, strict=True)
    fps: int = Field(default=30, ge=1, le=30, strict=True)
    duration_ticks: int = Field(default=120, ge=1, le=200, strict=True)
    interactions: tuple[VideoInteraction, ...] = (VideoInteraction(tick=20),)
    yaw: float = Field(default=45, ge=-360, le=360)
    pitch: float = Field(default=30, ge=-90, le=90)
    orbit_degrees: float = Field(default=180, ge=-360, le=360)
    zoom: float = Field(default=1, gt=0, le=16)

    @model_validator(mode="after")
    def check_interactions(self) -> Self:
        if not self.simulation_enabled and self.interactions:
            msg = "Static videos cannot contain input activations."
            raise ValueError(msg)
        if len(self.interactions) > 16 or any(item.tick >= self.duration_ticks for item in self.interactions):
            msg = "Video interactions must fit within the clip and contain at most 16 activations."
            raise ValueError(msg)
        if any(left.tick > right.tick for left, right in zip(self.interactions, self.interactions[1:], strict=False)):
            msg = "Video interactions must be in chronological order."
            raise ValueError(msg)
        return self


@runtime_checkable
class SchematicVideoRenderer(Protocol):
    """Optional capability supplied by subprocess and durable analyzers."""

    async def render_video(
        self, data: bytes, *, request: VideoRenderRequest, resource_pack: VerifiedResourcePack
    ) -> bytes: ...

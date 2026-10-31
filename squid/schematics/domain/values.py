"""Validated scalar and payload values used by schematic operations."""

import hashlib
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Self, SupportsFloat, cast

from squid.core.errors import ValidationError
from squid.core.i18n import tr

RESOURCE_PACK_MEDIA_TYPE: Literal["application/zip"] = "application/zip"


@dataclass(frozen=True, slots=True)
class RgbaColor:
    """Four finite normalized channels in red, green, blue, alpha order."""

    red: float
    green: float
    blue: float
    alpha: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(channel) and 0.0 <= channel <= 1.0 for channel in self):
            msg = tr(t"RGBA channels must be finite numbers between 0 and 1.")
            raise ValidationError(msg)

    @classmethod
    def from_channels(cls, channels: Sequence[object]) -> Self:
        """Decode exactly four numeric channels from a configuration or wire array."""
        if len(channels) != 4:
            msg = tr(t"RGBA colours require exactly four channels.")
            raise ValidationError(msg)
        try:
            return cls(*(float(cast(str | SupportsFloat, channel)) for channel in channels))
        except (TypeError, ValueError) as error:
            msg = tr(t"RGBA channels must be numbers.")
            raise ValidationError(msg) from error

    def __iter__(self) -> Iterator[float]:
        return iter(self.as_tuple())

    def as_tuple(self) -> tuple[float, float, float, float]:
        """Return the stable channel order used by render recipes and native adapters."""
        return self.red, self.green, self.blue, self.alpha


@dataclass(frozen=True, slots=True)
class VerifiedResourcePack:
    """Immutable resource-pack bytes with a verified SHA-256 and declared media type.

    The native renderer owns ZIP parsing. This value verifies transport identity and the
    media contract; it does not claim that arbitrary bytes form a valid Minecraft pack.
    """

    data: bytes
    sha256: str
    media_type: Literal["application/zip"] = RESOURCE_PACK_MEDIA_TYPE

    def __post_init__(self) -> None:
        if not isinstance(self.data, bytes):
            msg = tr(t"Resource-pack data must be immutable bytes.")
            raise ValidationError(msg)
        if self.media_type != RESOURCE_PACK_MEDIA_TYPE:
            media_type = RESOURCE_PACK_MEDIA_TYPE
            msg = tr(t"Resource packs must use {media_type}.")
            raise ValidationError(msg)
        actual = hashlib.sha256(self.data).hexdigest()
        if self.sha256 != actual:
            msg = tr(t"Resource-pack bytes do not match their SHA-256 digest.")
            raise ValidationError(msg)

    @classmethod
    def from_bytes(cls, data: bytes) -> Self:
        """Build a digest-verified resource-pack payload from immutable bytes."""
        return cls(data=data, sha256=hashlib.sha256(data).hexdigest())


__all__ = ["RESOURCE_PACK_MEDIA_TYPE", "RgbaColor", "VerifiedResourcePack"]

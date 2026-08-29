"""Portable colour policy resolved while semantic layouts are planned."""

from dataclasses import dataclass
from enum import StrEnum

from squid_layouts.primitives.styles import Color


class Tone(StrEnum):
    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


class AccentDefault(StrEnum):
    """How an omitted structural accent obtains its presentation colour."""

    INHERIT = "inherit"


INHERIT = AccentDefault.INHERIT
type Accent = Color | None | AccentDefault


@dataclass(frozen=True, slots=True)
class Palette:
    """Exact colours assigned to portable presentation roles."""

    brand: Color | None = None
    neutral: Color | None = None
    info: Color | None = 0x5865F2
    success: Color | None = 0x248046
    warning: Color | None = 0xF0B232
    danger: Color | None = 0xDA373C

    def tone(self, tone: Tone) -> Color | None:
        """Resolve one semantic tone without changing its meaning."""
        return {
            Tone.NEUTRAL: self.neutral,
            Tone.INFO: self.info,
            Tone.SUCCESS: self.success,
            Tone.WARNING: self.warning,
            Tone.DANGER: self.danger,
        }[tone]


DEFAULT_PALETTE = Palette()

__all__ = [
    "DEFAULT_PALETTE",
    "INHERIT",
    "Accent",
    "AccentDefault",
    "Palette",
    "Tone",
]

"""Portable colour policy resolved while semantic layouts are planned."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from squid_ui.primitives.styles import Color


class Tone(StrEnum):
    """Semantic colour roles that the active :class:`Palette` maps to exact colours."""

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"


class AccentDefault(StrEnum):
    """How an omitted structural accent obtains its presentation colour."""

    INHERIT = "inherit"
    """Resolve to the active palette's `brand`."""


INHERIT = AccentDefault.INHERIT
type Accent = Color | None | AccentDefault
"""An exact colour, `None` for no accent at all, or `INHERIT`."""


@dataclass(frozen=True, slots=True)
class Palette:
    """Exact colours assigned to portable presentation roles; `None` draws that role without a colour."""

    brand: Color | None = None
    """Accent a container with `INHERIT` resolves to."""
    neutral: Color | None = None
    info: Color | None = 0x5865F2
    success: Color | None = 0x248046
    warning: Color | None = 0xF0B232
    danger: Color | None = 0xDA373C

    def tone(self, tone: Tone) -> Color | None:
        """The colour assigned to `tone`."""
        return {
            Tone.NEUTRAL: self.neutral,
            Tone.INFO: self.info,
            Tone.SUCCESS: self.success,
            Tone.WARNING: self.warning,
            Tone.DANGER: self.danger,
        }[tone]


class PaletteRegistry:
    """Mutable table of named palettes with one default; a host builds it once and hands it to mounts.

    Every method raises `ValueError` for an empty name. The constructor and `set_default` raise
    `KeyError` when `default` is not registered.
    """

    def __init__(self, palettes: Mapping[str, Palette], *, default: str) -> None:
        self._palettes = dict(palettes)
        self._validate_name(default)
        if default not in self._palettes:
            message = f"default palette {default!r} is not registered"
            raise KeyError(message)
        self._default = default

    def register(self, name: str, palette: Palette) -> None:
        """Register or replace one named palette."""
        self._validate_name(name)
        self._palettes[name] = palette

    def resolve(self, name: str | None = None) -> Palette:
        """The palette registered as `name`, or the default when omitted; raises `KeyError` for an unknown name."""
        selected = self._default if name is None else name
        try:
            return self._palettes[selected]
        except KeyError:
            message = f"unknown palette {selected!r}"
            raise KeyError(message) from None

    def set_default(self, name: str) -> None:
        """Select the registered palette used by an unnamed resolution."""
        self._validate_name(name)
        if name not in self._palettes:
            message = f"unknown palette {name!r}"
            raise KeyError(message)
        self._default = name

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name:
            message = "palette names must not be empty"
            raise ValueError(message)


DEFAULT_PALETTE = Palette()

__all__ = [
    "DEFAULT_PALETTE",
    "INHERIT",
    "Accent",
    "AccentDefault",
    "Palette",
    "PaletteRegistry",
    "Tone",
]

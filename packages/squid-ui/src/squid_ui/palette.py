"""Portable colour policy resolved while semantic layouts are planned."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from squid_ui.primitives.styles import Color


class Tone(StrEnum):
    """Semantic colour roles that the active :class:`Palette` maps to exact colours."""

    NEUTRAL = "neutral"
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    DANGER = "danger"

    @property
    def base(self) -> Tone:
        """The built-in behavior targets use for this tone."""
        return self

    @property
    def status_marker(self) -> str:
        """Prefix used by targets that render status as plain text."""
        return {
            Tone.INFO: "\N{INFORMATION SOURCE}\N{VARIATION SELECTOR-16} ",
            Tone.SUCCESS: "✅ ",
            Tone.WARNING: "⚠️ ",
            Tone.DANGER: "❌ ",
        }.get(self, "")


@dataclass(frozen=True, slots=True)
class Role:
    """An application-defined meaning with a portable built-in fallback.

    Use a namespaced name such as ``build.pending`` to select a palette override.
    ``base`` supplies default colour, button style, and action grouping. ``marker``
    overrides a status prefix where supported; None inherits the base marker and
    an empty string suppresses it. Include any separating space in the marker.
    """

    name: str
    base: Tone
    marker: str | None = None

    @property
    def status_marker(self) -> str:
        """Resolve the optional marker against the built-in fallback."""
        return self.base.status_marker if self.marker is None else self.marker


class AccentRole(StrEnum):
    """Palette roles for structural accents that do not express a tone."""

    BRAND = "brand"


class AccentDefault(StrEnum):
    """How an omitted structural accent obtains its presentation colour."""

    INHERIT = "inherit"


INHERIT = AccentDefault.INHERIT
type Accent = Color | Tone | Role | AccentRole | AccentDefault | None


@dataclass(frozen=True, slots=True)
class Palette:
    """Exact colours assigned to portable presentation roles.

    ``roles`` overrides custom roles by name, including None to suppress colour.
    Missing names use the role's base tone. The supplied mapping is copied.
    """

    brand: Color | None = None
    neutral: Color | None = None
    info: Color | None = 0x5865F2
    success: Color | None = 0x248046
    warning: Color | None = 0xF0B232
    danger: Color | None = 0xDA373C

    roles: Mapping[str, Color | None] = field(default_factory=dict, hash=False)

    def __post_init__(self) -> None:
        # Palette inputs must not change underneath a cached plan.
        object.__setattr__(self, "roles", MappingProxyType(dict(self.roles)))

    def tone(self, tone: Tone | Role) -> Color | None:
        """Resolve one semantic tone without changing its meaning."""
        if isinstance(tone, Role):
            if tone.name in self.roles:
                return self.roles[tone.name]
            tone = tone.base
        return {
            Tone.NEUTRAL: self.neutral,
            Tone.INFO: self.info,
            Tone.SUCCESS: self.success,
            Tone.WARNING: self.warning,
            Tone.DANGER: self.danger,
        }[tone]


class PaletteRegistry:
    """Named palette choices for future planning and mount construction."""

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
        """Resolve a named palette, or the current default when omitted."""
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
    "AccentRole",
    "Palette",
    "PaletteRegistry",
    "Role",
    "Tone",
]

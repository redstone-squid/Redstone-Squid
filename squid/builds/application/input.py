"""Parse human-entered build facts independently of their input interface."""

import re

from squid.builds.domain.models import parse_time_string
from squid.core.errors import ValidationError
from squid.core.i18n import tr

_BARE_NUMBER = re.compile(r"[~≈]?\s*[+-]?(?:\d+(?:\.\d+)?|\.\d+)")


def parse_build_ticks(text: str) -> int:
    """Parse "30gt", "15rt", or "1.5s" into game ticks, insisting on a unit.

    parse_time_string reads a bare number as seconds, but submitters often mean ticks,
    so the form asks for the unit instead of guessing. The manifest's help text says so too.
    """
    if _BARE_NUMBER.fullmatch(text.strip()):
        message = tr(t"build-input-time-unit = Add a unit: gt for game ticks, rt for redstone ticks, or s for seconds.")
        raise ValidationError(message)
    ticks = parse_time_string(text)
    if ticks is None or ticks < 0:
        message = tr(t"build-input-time-invalid = Enter a time such as 30gt, 15rt, or 1.5s.")
        raise ValidationError(message)
    return ticks


def normalize_extender_orientation(value: str | None) -> str | None:
    """Normalize an optional extender direction to the stored spelling."""
    normalized = (value.strip().title() or None) if value is not None else None
    if normalized not in {None, "Horizontal", "Up", "Down"}:
        message = tr(t"build-input-extender-orientation = Choose Horizontal, Up, or Down.")
        raise ValidationError(message)
    return normalized


def validate_extension_length(value: int | None) -> int | None:
    """Validate a supplied extender length while allowing an explicit clear."""
    if value is not None and (isinstance(value, bool) or not 1 <= value <= 512):
        message = tr(t"build-input-extension-length = Extension length must be between 1 and 512 blocks.")
        raise ValidationError(message)
    return value

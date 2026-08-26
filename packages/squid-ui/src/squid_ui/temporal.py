"""Portable exact-time values and local-time resolution."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AmbiguousTimeMode(StrEnum):
    """How a repeated local time maps to one of its two instants."""

    REJECT = "reject"
    EARLIER = "earlier"
    LATER = "later"


class NonexistentTimeMode(StrEnum):
    """How a local time skipped by an offset transition is handled."""

    REJECT = "reject"
    SHIFT_FORWARD = "shift_forward"


class AmbiguousLocalTimeError(ValueError):
    """A wall time maps to two instants and its policy rejects both."""


class NonexistentLocalTimeError(ValueError):
    """A wall time maps to no instant and its policy rejects adjustment."""


class InvalidTimezoneOffsetError(ValueError):
    """An explicit offset is not valid for a wall time in its named zone."""


@dataclass(frozen=True, slots=True)
class ZonedDateTime:
    """One exact instant together with the IANA timezone used to present it."""

    instant: datetime
    timezone: str
    _zone: ZoneInfo = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.instant.tzinfo is None or self.instant.utcoffset() is None:
            message = "ZonedDateTime instant must be aware"
            raise ValueError(message)
        zone = _zone(self.timezone)
        object.__setattr__(self, "instant", self.instant.astimezone(UTC))
        object.__setattr__(self, "_zone", zone)

    @property
    def local(self) -> datetime:
        """Return the instant projected into its named timezone."""
        return self.instant.astimezone(self._zone)

    def isoformat(self) -> str:
        """Return an exact local representation retaining the IANA zone key."""
        return f"{self.local.isoformat(sep=' ')}[{self.timezone}]"


def timezone_from_name(name: str) -> ZoneInfo:
    """Validate and load one IANA timezone name."""
    return _zone(name)


def resolve_local_datetime(
    value: datetime,
    timezone: tzinfo,
    ambiguous: AmbiguousTimeMode,
    nonexistent: NonexistentTimeMode,
) -> datetime:
    """Resolve a naive wall time to one aware instant under explicit policies."""
    valid, resolved = _local_mapping(value, timezone)
    if valid:
        if len(valid) == 1:
            return valid[0]
        if ambiguous is AmbiguousTimeMode.REJECT:
            raise AmbiguousLocalTimeError
        key = lambda candidate: candidate.astimezone(UTC)
        return min(valid, key=key) if ambiguous is AmbiguousTimeMode.EARLIER else max(valid, key=key)

    if nonexistent is NonexistentTimeMode.REJECT:
        raise NonexistentLocalTimeError
    shifted = tuple(localized for localized in resolved if localized.replace(tzinfo=None) > value)
    if not shifted:
        raise NonexistentLocalTimeError
    return min(shifted, key=lambda localized: localized.replace(tzinfo=None))


def resolve_offset_datetime(value: datetime, timezone: tzinfo) -> datetime:
    """Resolve aware input only when its offset is valid in the named timezone."""
    wall = value.replace(tzinfo=None)
    valid, _ = _local_mapping(wall, timezone)
    offset = value.utcoffset()
    for candidate in valid:
        if candidate.utcoffset() == offset:
            return candidate
    raise InvalidTimezoneOffsetError


def _local_mapping(value: datetime, timezone: tzinfo) -> tuple[tuple[datetime, ...], tuple[datetime, ...]]:
    candidates = tuple(value.replace(tzinfo=timezone, fold=fold) for fold in (0, 1))
    instants = tuple(candidate.astimezone(UTC) for candidate in candidates)
    resolved = tuple(instant.astimezone(timezone) for instant in instants)
    valid_by_instant = {
        instant: localized
        for instant, localized in zip(instants, resolved, strict=True)
        if localized.replace(tzinfo=None) == value
    }
    return tuple(valid_by_instant.values()), resolved


def _zone(name: str) -> ZoneInfo:
    if not isinstance(name, str):
        message = "timezone must be an IANA timezone name"
        raise TypeError(message)
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as error:
        message = f"unknown IANA timezone {name!r}"
        raise ValueError(message) from error


__all__ = [
    "AmbiguousLocalTimeError",
    "AmbiguousTimeMode",
    "InvalidTimezoneOffsetError",
    "NonexistentLocalTimeError",
    "NonexistentTimeMode",
    "ZonedDateTime",
    "resolve_local_datetime",
    "resolve_offset_datetime",
    "timezone_from_name",
]

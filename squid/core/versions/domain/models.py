"""Minecraft version domain values."""

import re
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Literal, override

from squid.core.sentinel import UNSET
from squid.core.versions.errors import InvalidVersionError

Edition = Literal["Java", "Bedrock"]
VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)(?:\.(\d+))?\W*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MinecraftVersion:
    """A Minecraft edition and three release-number components."""

    edition: Edition
    major: int
    minor: int
    patch: int

    def __post_init__(self) -> None:
        if min(self.major, self.minor, self.patch) < 0:
            msg = "Release numbers cannot be negative."
            raise InvalidVersionError(msg, context={"version": str(self)})

    @override
    def __str__(self) -> str:
        return f"{self.edition} {self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class VersionEntry:
    """A release in the catalogue, with the world-format number it writes, if known."""

    id: int
    version: MinecraftVersion
    data_version: int | None = None

    @override
    def __str__(self) -> str:
        return str(self.version)


type Patch[T] = T | UNSET


@dataclass(frozen=True, slots=True)
class VersionCorrection:
    """Fields to change on a catalogue entry. Clearing `data_version` and omitting it differ."""

    edition: Patch[Edition] = UNSET
    major: Patch[int] = UNSET
    minor: Patch[int] = UNSET
    patch: Patch[int] = UNSET
    data_version: Patch[int | None] = UNSET

    def apply(self, entry: VersionEntry) -> VersionEntry:
        """Return `entry` with the supplied fields replaced."""
        changes = {
            name: value
            for name in ("edition", "major", "minor", "patch")
            if (value := getattr(self, name)) is not UNSET
        }
        data_version = entry.data_version if self.data_version is UNSET else self.data_version
        return replace(entry, version=replace(entry.version, **changes), data_version=data_version)


def parse_version_string(version_string: str) -> tuple[Edition, int, int, int]:
    """Parse an edition and release number, such as `Bedrock 1.21.50`.

    Edition names are case-insensitive. Missing editions default to Java and
    missing patch numbers to zero, so `1.21` becomes ("Java", 1, 21, 0).
    Surrounding punctuation and whitespace are allowed. Invalid syntax raises
    InvalidVersionError; this function does not check the version catalog.
    """
    match = VERSION_PATTERN.match(version_string)
    if not match:
        msg = "Invalid version string format."
        raise InvalidVersionError(msg, context={"version": version_string})

    edition, major, minor, patch = match.groups()
    parsed_edition: Edition = "Bedrock" if edition is not None and edition.lower() == "bedrock" else "Java"
    return parsed_edition, int(major), int(minor), int(patch or 0)


class VersionListSort(StrEnum):
    ID = "id"
    RELEASE = "release"

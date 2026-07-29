"""Minecraft version domain values."""

import re
from dataclasses import dataclass
from typing import Literal, override

from squid.exceptions import InvalidVersionError

Edition = Literal["Java", "Bedrock"]
VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)(?:\.(\d+))?\W*$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class MinecraftVersion:
    """A Minecraft edition and semantic version."""

    edition: Edition
    major: int
    minor: int
    patch: int

    @override
    def __str__(self) -> str:
        return f"{self.edition} {self.major}.{self.minor}.{self.patch}"


def parse_version_string(version_string: str) -> tuple[Edition, int, int, int]:
    """Parse a Minecraft version, defaulting to Java when the edition is omitted."""
    match = VERSION_PATTERN.match(version_string)
    if not match:
        msg = "Invalid version string format."
        raise InvalidVersionError(msg, context={"version": version_string})

    edition, major, minor, patch = match.groups()
    parsed_edition: Edition = "Bedrock" if edition is not None and edition.lower() == "bedrock" else "Java"
    return parsed_edition, int(major), int(minor), int(patch or 0)

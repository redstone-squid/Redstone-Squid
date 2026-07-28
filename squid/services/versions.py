"""Framework-independent Minecraft version application service."""

import re
from dataclasses import dataclass
from typing import Literal, Protocol, override

Edition = Literal["Java", "Bedrock"]
VERSION_PATTERN = re.compile(r"^\W*(Java|Bedrock)? ?(\d+)\.(\d+)(?:\.(\d+))?\W*$", re.IGNORECASE)


def parse_version_string(version_string: str) -> tuple[Edition, int, int, int]:
    """Parse a Minecraft version, defaulting to Java when the edition is omitted."""
    match = VERSION_PATTERN.match(version_string)
    if not match:
        msg = "Invalid version string format."
        raise ValueError(msg)

    edition, major, minor, patch = match.groups()
    parsed_edition: Edition = "Bedrock" if edition is not None and edition.lower() == "bedrock" else "Java"
    return parsed_edition, int(major), int(minor), int(patch or 0)


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


class VersionRepository(Protocol):
    """Persistence operations required by :class:`VersionService`."""

    async def add(self, version: MinecraftVersion) -> MinecraftVersion: ...

    async def list(self, edition: Edition) -> list[MinecraftVersion]: ...


class VersionService:
    """Parse, store, and list recognized Minecraft versions."""

    def __init__(self, repository: VersionRepository):
        self._repository = repository

    async def add(self, version_string: str, *, edition: Edition | None = None) -> MinecraftVersion:
        parsed_edition, major, minor, patch = parse_version_string(version_string)
        version = MinecraftVersion(edition or parsed_edition, major, minor, patch)
        return await self._repository.add(version)

    async def list_versions(self, edition: Edition) -> list[MinecraftVersion]:
        return await self._repository.list(edition)

    async def list_display(self, edition: Edition, *, limit: int = 20) -> list[str]:
        return [str(version) for version in (await self.list_versions(edition))[:limit]]

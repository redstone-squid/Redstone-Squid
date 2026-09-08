"""Application ports for Minecraft versions."""

from typing import Protocol

from squid.versions.domain import Edition, MinecraftVersion


class VersionRepository(Protocol):
    """Persistence operations required by `VersionService`."""

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        """Insert the version and return it as stored."""
        ...

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        """The edition's known versions, oldest first.

        Raises `DataIntegrityError` if a stored row names an edition this build does not support.
        """
        ...

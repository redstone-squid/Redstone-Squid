"""Application ports for Minecraft versions."""

from typing import Protocol

from squid.versions.domain import Edition, MinecraftVersion


class VersionRepository(Protocol):
    """Persistence operations required by :class:`VersionService`."""

    async def add(self, version: MinecraftVersion) -> MinecraftVersion: ...

    async def list(self, edition: Edition) -> list[MinecraftVersion]: ...

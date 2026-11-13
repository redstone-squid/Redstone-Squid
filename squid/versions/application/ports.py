"""Application ports for Minecraft versions."""

from typing import Protocol

from squid.unit_of_work import UnitOfWork
from squid.versions.domain import Edition, MinecraftVersion


class VersionRepository(Protocol):
    """Persistence operations required by :class:`VersionService`."""

    async def add(self, version: MinecraftVersion) -> MinecraftVersion: ...

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        """Return versions ordered by major, minor, then patch number, ascending."""
        ...

    async def data_version_for(self, version_label: str) -> int | None: ...

    async def label_for_data_version(self, data_version: int) -> str | None: ...


class VersionUnitOfWork(UnitOfWork, Protocol):
    """Transaction and repositories used by VersionService."""

    @property
    def versions(self) -> VersionRepository: ...

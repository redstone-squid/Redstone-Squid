"""Application ports for Minecraft versions."""

from typing import Protocol

from squid.core.builds.application.ports import BuildEditingUnitOfWork
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.versions.domain import Edition, MinecraftVersion, VersionEntry


class VersionRepository(Protocol):
    """Persistence operations required by :class:`VersionService`."""

    async def add(self, version: MinecraftVersion) -> VersionEntry: ...

    async def get(self, version_id: int) -> VersionEntry | None: ...

    async def update(self, entry: VersionEntry) -> VersionEntry | None: ...

    async def delete(self, version_id: int) -> bool: ...

    async def list(self, edition: Edition) -> list[VersionEntry]:
        """Return versions ordered by major, minor, then patch number, ascending."""
        ...

    async def data_version_for(self, version_label: str) -> int | None: ...

    async def label_for_data_version(self, data_version: int) -> str | None: ...


class VersionUnitOfWork(PermissionUnitOfWork, BuildEditingUnitOfWork, Protocol):
    """Transaction and repositories used by VersionService."""

    @property
    def versions(self) -> VersionRepository: ...

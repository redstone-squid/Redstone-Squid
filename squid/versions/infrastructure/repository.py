"""SQLAlchemy persistence adapter for Minecraft versions."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import DataIntegrityError
from squid.versions.domain import Edition, MinecraftVersion
from squid.versions.models import Version


class VersionRepository:
    """Persist and query Minecraft versions."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        self._session().add(
            Version(
                edition=version.edition,
                major_version=version.major,
                minor_version=version.minor,
                patch_number=version.patch,
            )
        )
        return version

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        statement = (
            select(Version)
            .where(Version.edition == edition)
            .order_by(Version.major_version, Version.minor_version, Version.patch_number)
        )
        versions = (await self._session().scalars(statement)).all()
        return [
            MinecraftVersion(
                edition=self._to_edition(version.edition),
                major=version.major_version,
                minor=version.minor_version,
                patch=version.patch_number,
            )
            for version in versions
        ]

    @staticmethod
    def _to_edition(value: str) -> Edition:
        if value == "Java" or value == "Bedrock":
            return value
        msg = f"Unsupported Minecraft edition in database: {value!r}"
        raise DataIntegrityError(msg, context={"edition": value})

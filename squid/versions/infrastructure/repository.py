"""SQLAlchemy persistence adapter for Minecraft versions."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import DataIntegrityError
from squid.versions.domain import Edition, MinecraftVersion, parse_version_string
from squid.versions.errors import InvalidVersionError
from squid.versions.models import Version


class VersionRepository:
    """Stage versions and query the catalog in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        """Stage a new version; the caller must commit to save it."""
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

    async def data_version_for(self, version_label: str) -> int | None:
        """Return the data version, or None for an invalid label, unknown release, or missing value."""
        try:
            edition, major, minor, patch = parse_version_string(version_label)
        except InvalidVersionError:
            return None

        statement = select(Version.data_version).where(
            Version.edition == edition,
            Version.major_version == major,
            Version.minor_version == minor,
            Version.patch_number == patch,
        )
        session = self._session()
        return await session.scalar(statement)

    async def label_for_data_version(self, data_version: int) -> str | None:
        """Return the earliest matching catalogue release, or None if no release matches.

        Order matches by major, minor, and patch version because multiple releases can share a data version.
        """
        statement = (
            select(Version)
            .where(Version.data_version == data_version)
            .order_by(Version.major_version, Version.minor_version, Version.patch_number)
            .limit(1)
        )
        session = self._session()
        version = await session.scalar(statement)
        if version is None:
            return None
        return f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"

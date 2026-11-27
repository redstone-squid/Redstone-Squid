"""SQLAlchemy persistence adapter for Minecraft versions."""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from squid.core.errors import DataIntegrityError
from squid.core.versions.domain import Edition, MinecraftVersion, VersionEntry, parse_version_string
from squid.core.versions.errors import InvalidVersionError, VersionExistsError, VersionInUseError
from squid.core.versions.models import Version

_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"


class VersionRepository:
    """Stage versions and query the catalog in the caller's transaction."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

    async def add(self, version: MinecraftVersion) -> VersionEntry:
        """Stage a new version; the caller must commit to save it.

        Raises VersionExistsError when the catalogue already has the release.
        """
        row = Version(
            edition=version.edition,
            major_version=version.major,
            minor_version=version.minor,
            patch_number=version.patch,
        )
        self._session().add(row)
        await self._flush(version)
        return self._to_entry(row)

    async def get(self, version_id: int) -> VersionEntry | None:
        row = await self._session().get(Version, version_id)
        return None if row is None else self._to_entry(row)

    async def update(self, entry: VersionEntry) -> VersionEntry | None:
        """Overwrite an entry in place, so builds and records that use it follow the correction.

        Returns None when the entry does not exist. Raises VersionExistsError when
        the corrected release duplicates another entry.
        """
        row = await self._session().get(Version, entry.id)
        if row is None:
            return None
        row.edition = entry.version.edition
        row.major_version = entry.version.major
        row.minor_version = entry.version.minor
        row.patch_number = entry.version.patch
        row.data_version = entry.data_version
        await self._flush(entry.version)
        return self._to_entry(row)

    async def delete(self, version_id: int) -> bool:
        """Delete an entry, returning False when it does not exist.

        Raises VersionInUseError when builds or records still refer to it.
        """
        session = self._session()
        row = await session.get(Version, version_id)
        if row is None:
            return False
        await session.delete(row)
        try:
            await session.flush()
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == _FOREIGN_KEY_VIOLATION:
                raise VersionInUseError(version_id) from error
            raise
        return True

    async def list(self, edition: Edition) -> list[VersionEntry]:
        statement = (
            select(Version)
            .where(Version.edition == edition)
            .order_by(Version.major_version, Version.minor_version, Version.patch_number)
        )
        return [self._to_entry(row) for row in (await self._session().scalars(statement)).all()]

    async def _flush(self, version: MinecraftVersion) -> None:
        try:
            await self._session().flush()
        except IntegrityError as error:
            if getattr(error.orig, "sqlstate", None) == _UNIQUE_VIOLATION:
                raise VersionExistsError(str(version)) from error
            raise

    @classmethod
    def _to_entry(cls, row: Version) -> VersionEntry:
        return VersionEntry(
            id=row.id,
            version=MinecraftVersion(
                edition=cls._to_edition(row.edition),
                major=row.major_version,
                minor=row.minor_version,
                patch=row.patch_number,
            ),
            data_version=row.data_version,
        )

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

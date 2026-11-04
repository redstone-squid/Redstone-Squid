"""Translate human-readable Minecraft releases into schematic data versions.

Users choose labels such as Java 1.20.4; the native converter needs the numeric data version that
identifies Minecraft's saved-data format. The shared version catalogue stores that correspondence.
Several releases can share a data version, so reverse lookup returns the earliest matching release.
"""

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.versions.domain import parse_version_string
from squid.versions.errors import InvalidVersionError
from squid.versions.models import Version


class PostgresSchematicVersionResolver:
    """Resolve data versions from the shared version catalogue."""

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session

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

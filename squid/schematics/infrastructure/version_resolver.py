"""Translation between Minecraft version labels and numeric data versions.

`convert` needs a data version, but every version a user can name in this application is a
label like `Java 1.20.4`. The mapping is a fixed published fact, so it lives in the `versions`
table as one more column rather than in a service that has to be kept in sync.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.versions.domain import parse_version_string
from squid.versions.errors import InvalidVersionError
from squid.versions.infrastructure.models import Version


class PostgresSchematicVersionResolver:
    """Resolve data versions from the shared version catalogue."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def data_version_for(self, version_label: str) -> int | None:
        """Return the numeric data version for a label, or `None` if we do not know one.

        A label we cannot even parse is answered with `None` rather than an exception: callers
        are choosing a conversion target, and "we have no data version for that" is a normal
        answer they already have to handle.
        """
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
        async with self._session_factory() as session:
            return await session.scalar(statement)

    async def label_for_data_version(self, data_version: int) -> str | None:
        """Return the version label for a data version, preferring the earliest release.

        Several patch releases can share one data version; the first is the one that
        introduced it and the one worth naming.
        """
        statement = (
            select(Version)
            .where(Version.data_version == data_version)
            .order_by(Version.major_version, Version.minor_version, Version.patch_number)
            .limit(1)
        )
        async with self._session_factory() as session:
            version = await session.scalar(statement)
        if version is None:
            return None
        return f"{version.edition} {version.major_version}.{version.minor_version}.{version.patch_number}"

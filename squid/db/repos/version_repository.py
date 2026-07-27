"""SQLAlchemy repository for Minecraft versions."""

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import Version
from squid.services.versions import Edition, MinecraftVersion


class VersionRepository:
    """Persist and query Minecraft versions."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self._session = session

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        async with self._session() as session:
            stmt = (
                insert(Version)
                .values(
                    edition=version.edition,
                    major_version=version.major,
                    minor_version=version.minor,
                    patch_number=version.patch,
                )
                .returning(Version)
            )
            stored = (await session.execute(stmt)).scalar_one()
            await session.commit()
            return MinecraftVersion(
                edition=self._to_edition(stored.edition),
                major=stored.major_version,
                minor=stored.minor_version,
                patch=stored.patch_number,
            )

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        async with self._session() as session:
            stmt = (
                select(Version)
                .where(Version.edition == edition)
                .order_by(Version.major_version, Version.minor_version, Version.patch_number)
            )
            versions = (await session.execute(stmt)).scalars().all()
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
        raise ValueError(msg)

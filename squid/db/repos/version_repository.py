"""SQLAlchemy repository for Minecraft versions."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.repos._model_repos import _VersionModelRepository
from squid.db.schema import Version
from squid.services.versions import Edition, MinecraftVersion


class VersionRepository:
    """Persist and query Minecraft versions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def add(self, version: MinecraftVersion) -> MinecraftVersion:
        async with self._session_factory() as session:
            repository = _VersionModelRepository(session=session, auto_commit=True)
            stored = await repository.add(
                Version(
                    edition=version.edition,
                    major_version=version.major,
                    minor_version=version.minor,
                    patch_number=version.patch,
                )
            )
            return MinecraftVersion(
                edition=self._to_edition(stored.edition),
                major=stored.major_version,
                minor=stored.minor_version,
                patch=stored.patch_number,
            )

    async def list(self, edition: Edition) -> list[MinecraftVersion]:
        async with self._session_factory() as session:
            repository = _VersionModelRepository(session=session)
            versions = await repository.get_many(
                Version.edition == edition,
                order_by=[
                    (Version.major_version, False),
                    (Version.minor_version, False),
                    (Version.patch_number, False),
                ],
            )
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

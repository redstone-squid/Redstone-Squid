"""SQLAlchemy persistence adapter for Minecraft versions."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import DataIntegrityError
from squid.persistence.models import register_models
from squid.persistence.repository import BaseAsyncRepository
from squid.versions.domain import Edition, MinecraftVersion
from squid.versions.infrastructure.models import Version

register_models()


class _VersionModelRepository(BaseAsyncRepository[Version]):
    model_type = Version


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
        raise DataIntegrityError(msg, context={"edition": value})

"""Advanced-alchemy repository for build restriction metadata."""

from advanced_alchemy.exceptions import DuplicateKeyError
from async_lru import alru_cache
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.application import RestrictionDefinition
from squid.builds.errors import AliasAlreadyAddedError, AliasInUseError, RestrictionNotFoundError
from squid.builds.infrastructure.models import Restriction, RestrictionAlias
from squid.persistence.repositories import RestrictionAliasModelRepository, RestrictionModelRepository


class RestrictionRepository:
    """Convert persistence restriction rows into application values."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    @alru_cache
    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        async with self._session_factory() as session:
            repository = RestrictionModelRepository(session=session)
            restrictions = await repository.get_many()
            return [RestrictionDefinition(row.name, row.type) for row in restrictions]

    async def add_alias(self, restriction: str, alias: str) -> None:
        async with self._session_factory() as session:
            restriction_repository = RestrictionModelRepository(session=session)
            alias_repository = RestrictionAliasModelRepository(session=session, auto_commit=True)
            restriction_id = await self._get_restriction_id(
                restriction_repository,
                alias_repository,
                restriction,
            )
            alias_restriction_id = await self._get_restriction_id(
                restriction_repository,
                alias_repository,
                alias,
            )
            if restriction_id is None:
                raise RestrictionNotFoundError(restriction)
            if alias_restriction_id == restriction_id:
                raise AliasAlreadyAddedError(alias, restriction_id)
            if alias_restriction_id is not None:
                raise AliasInUseError(alias, alias_restriction_id)

            try:
                await alias_repository.add(RestrictionAlias(restriction_id=restriction_id, alias=alias))
            except DuplicateKeyError as exc:
                raise AliasAlreadyAddedError(alias, restriction_id) from exc

    @staticmethod
    async def _get_restriction_id(
        restriction_repository: RestrictionModelRepository,
        alias_repository: RestrictionAliasModelRepository,
        name_or_alias: str,
    ) -> int | None:
        restriction = await restriction_repository.get_one_or_none(Restriction.name.ilike(f"%{name_or_alias}%"))
        if restriction is not None:
            return restriction.id
        alias = await alias_repository.get_one_or_none(RestrictionAlias.alias.ilike(f"%{name_or_alias}%"))
        return None if alias is None else alias.restriction_id

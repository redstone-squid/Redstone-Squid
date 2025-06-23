"""Functions for build types and restrictions."""

import asyncio

from async_lru import alru_cache
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.db.schema import Restriction, RestrictionAlias


class RestrictionError(Exception):
    """Base for *all* restriction/alias problems."""


class RestrictionNotFound(RestrictionError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Restriction '{name}' does not exist")


class AliasAlreadyAdded(RestrictionError):
    def __init__(self, alias: str, restriction_id: int) -> None:
        self.alias = alias
        self.restriction_id = restriction_id
        super().__init__(f"Alias '{alias}' is already on restriction {restriction_id}")


class AliasTakenByOther(RestrictionError):
    def __init__(self, alias: str, other_id: int) -> None:
        self.alias = alias
        self.other_id = other_id
        super().__init__(f"Alias '{alias}' belongs to restriction {other_id}")


class BuildTagsManager:
    """A class for managing build tags and restrictions."""

    def __init__(self, session: async_sessionmaker[AsyncSession]):
        self.session = session

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        """Find a restriction by its name or alias.

        Args:
            name_or_alias (str): The name or alias of the restriction.

        Returns:
            The ID of the restriction if found, otherwise None.
        """
        async with self.session() as session:
            # Try to find by name
            stmt = select(Restriction).where(Restriction.name.ilike(f"%{name_or_alias}%"))
            result = await session.execute(stmt)
            restriction = result.scalar_one_or_none()
            if restriction:
                return restriction.id

            # Try to find by alias
            stmt = select(RestrictionAlias).where(RestrictionAlias.alias.ilike(f"%{name_or_alias}%"))
            result = await session.execute(stmt)
            alias = result.scalar_one_or_none()
            if alias:
                return alias.restriction_id

            return None

    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None:
        """Add an alias for a restriction by its ID.

        Args:
            restriction_id (int): The ID of the restriction.
            alias (str): The alias to add.
        """
        async with self.session() as session:
            try:
                restriction_alias = RestrictionAlias(restriction_id=restriction_id, alias=alias)
                session.add(restriction_alias)
                await session.commit()
            except IntegrityError:
                # Likely because the alias is already taken by another restriction.
                await session.rollback()
                alias_rid = await self.get_restriction_id(alias)
                assert alias_rid is not None
                raise AliasAlreadyAdded(alias, alias_rid)

    async def add_restriction_alias(self, name_or_alias: str, alias: str) -> None:
        """Add an alias for a restriction by its name or alias.

        Args:
            name_or_alias (str): The name or alias of the restriction.
            alias (str): The alias to add.

        Raises:
            RestrictionNotFound: If the restriction does not exist.
            AliasAlreadyAdded: If the alias is already added to the restriction.
            AliasTakenByOther: If the alias is already taken by another restriction.
        """
        rid, alias_rid = await asyncio.gather(self.get_restriction_id(name_or_alias), self.get_restriction_id(alias))
        if rid is None:
            raise RestrictionNotFound(name_or_alias)

        if alias_rid is not None:
            if alias_rid == rid:
                raise AliasAlreadyAdded(alias, rid)
            raise AliasTakenByOther(alias, alias_rid)

        await self.add_restriction_alias_by_id(rid, alias)

    # TODO: Invalidate cache every, say, 1 day (or make supabase callback whenever the table is updated)
    @alru_cache
    async def fetch_all_restrictions(self) -> list[Restriction]:
        """Fetches all restrictions from the database."""
        async with self.session() as session:
            result = await session.execute(select(Restriction))
            return list(result.scalars().all())

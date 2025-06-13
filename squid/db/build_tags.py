"""Functions for build types and restrictions."""

import asyncio

from postgrest.exceptions import APIError

from supabase import AsyncClient


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

    def __init__(self, client: AsyncClient):
        self.client = client

    async def get_restriction_id(self, name_or_alias: str) -> int | None:
        """Find a restriction by its name or alias.

        Args:
            name_or_alias (str): The name or alias of the restriction.

        Returns:
            The ID of the restriction if found, otherwise None.
        """
        restrictions_query = (
            await self.client.table("restrictions").select("id").ilike("name", f"%{name_or_alias}%").execute()
        )
        if restrictions_query.data:
            return restrictions_query.data[0]["id"]

        aliases_query = (
            await self.client.table("restriction_aliases")
            .select("restriction_id")
            .ilike("alias", f"%{name_or_alias}%")
            .execute()
        )
        if aliases_query.data:
            return aliases_query.data[0]["restriction_id"]

        return None

    async def add_restriction_alias_by_id(self, restriction_id: int, alias: str) -> None:
        """Add an alias for a restriction by its ID.

        Args:
            restriction_id (int): The ID of the restriction.
            alias (str): The alias to add.
        """
        try:
            await (
                self.client.table("restriction_aliases")
                .insert({"restriction_id": restriction_id, "alias": alias})
                .execute()
            )
        except APIError as e:
            if e.code == "23505":  # Unique violation error
                alias_rid = await self.get_restriction_id(alias)
                assert alias_rid is not None
                raise AliasAlreadyAdded(alias, alias_rid)
            else:
                raise e

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

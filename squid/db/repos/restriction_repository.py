"""Adapter exposing build restriction metadata to application services."""

from squid.db.build_tags import BuildTagsManager
from squid.services.builds import RestrictionDefinition


class RestrictionRepository:
    """Convert persistence restriction rows into application values."""

    def __init__(self, manager: BuildTagsManager):
        self._manager = manager

    async def fetch_all_restrictions(self) -> list[RestrictionDefinition]:
        rows = await self._manager.fetch_all_restrictions()
        return [RestrictionDefinition(row.name, row.type) for row in rows]

    async def add_alias(self, restriction: str, alias: str) -> None:
        await self._manager.add_restriction_alias(restriction, alias)

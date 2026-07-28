"""Framework-neutral application queries for builds."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.db.builds import Build
from squid.db.schema import SmallestDoor


@dataclass(frozen=True, slots=True)
class RestrictionSearchItem:
    """A restriction name or alias returned by a substring search."""

    restriction_id: int
    name: str
    is_alias: bool


class BuildQueryRepository(Protocol):
    """Build persistence queries required by search workflows."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def search_smallest_door_records(
        self, query: str, *, limit: int
    ) -> list[tuple[SmallestDoor, float, int]]: ...

    async def get_pending(self) -> list[Build]: ...


class BuildMetadataQueries(Protocol):
    """Restriction and pattern metadata queries."""

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]: ...

    async def list_patterns(self) -> list[str]: ...


class SemanticBuildSearch(Protocol):
    """Natural-language build lookup."""

    async def find_build_id(self, query: str) -> int | None: ...


class BuildQueryService:
    """Coordinate build, restriction, pattern, and semantic queries."""

    def __init__(
        self,
        builds: BuildQueryRepository,
        metadata: BuildMetadataQueries,
        semantic_search: SemanticBuildSearch,
    ):
        self._builds = builds
        self._metadata = metadata
        self._semantic_search = semantic_search

    async def get(self, build_id: int) -> Build | None:
        return await self._builds.get_by_id(build_id)

    async def pending(self) -> Sequence[Build]:
        return await self._builds.get_pending()

    async def search_records(self, query: str) -> list[tuple[SmallestDoor, float, int]]:
        return await self._builds.search_smallest_door_records(query, limit=11)

    async def restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        return await self._metadata.search_restrictions(query)

    async def patterns(self) -> list[str]:
        return await self._metadata.list_patterns()

    async def semantic(self, query: str) -> Build | None:
        build_id = await self._semantic_search.find_build_id(query)
        if build_id is None:
            return None
        return await self._builds.get_by_id(build_id)

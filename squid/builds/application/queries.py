"""Framework-neutral application queries for builds."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from squid.builds.domain import Build, Status


@dataclass(frozen=True, slots=True)
class RestrictionSearchItem:
    """A restriction name or alias returned by a substring search."""

    restriction_id: int
    name: str
    is_alias: bool


class BuildQueryRepository(Protocol):
    """Build persistence queries required by search workflows."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]: ...

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
        submitter_id: int | None,
        submitter_account_id: int | None,
        after_id: int | None,
        limit: int,
    ) -> list[Build]: ...

    async def get_pending(self) -> list[Build]: ...


class BuildMetadataQueries(Protocol):
    """Restriction and pattern metadata queries."""

    async def search_restrictions(self, query: str | None) -> list[RestrictionSearchItem]: ...

    async def list_patterns(self) -> list[str]: ...

    async def search_patterns(self, query: str, limit: int = 25) -> list[tuple[str, float, int]]: ...


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

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]:
        """Load builds in the caller's requested order, omitting missing rows."""
        return await self._builds.get_many(build_ids)

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
        submitter_id: int | None = None,
        submitter_account_id: int | None = None,
        after_id: int | None = None,
        limit: int = 21,
    ) -> list[Build]:
        """List an authoritative descending-ID page under a visibility policy."""
        return await self._builds.list_page(
            statuses=statuses,
            submitter_id=submitter_id,
            submitter_account_id=submitter_account_id,
            after_id=after_id,
            limit=limit,
        )

    async def restrictions(self, query: str | None) -> list[RestrictionSearchItem]:
        return await self._metadata.search_restrictions(query)

    async def patterns(self) -> list[str]:
        return await self._metadata.list_patterns()

    async def search_patterns(self, query: str) -> list[tuple[str, float, int]]:
        return await self._metadata.search_patterns(query)

    async def semantic(self, query: str) -> Build | None:
        build_id = await self._semantic_search.find_build_id(query)
        if build_id is None:
            return None
        return await self._builds.get_by_id(build_id)

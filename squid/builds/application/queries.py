"""Framework-neutral application queries for builds."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from squid.builds.domain import Build, Status


@dataclass(frozen=True, slots=True)
class RestrictionSearchItem:
    """A restriction name or alias returned by a substring search."""

    restriction_id: int
    name: str
    is_alias: bool


@dataclass(frozen=True, slots=True)
class BuildListSort:
    """Display ordering for an authoritative build listing."""

    field: Literal["id", "submission_time"] = "id"
    descending: bool = True


DEFAULT_BUILD_LIST_SORT = BuildListSort()


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
        sort: BuildListSort,
        offset: int,
        after_id: int | None,
        before_id: int | None,
        limit: int,
    ) -> list[Build]: ...

    async def count(
        self,
        *,
        statuses: frozenset[Status],
        submitter_id: int | None,
        submitter_account_id: int | None,
    ) -> int: ...

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
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        offset: int = 0,
        after_id: int | None = None,
        before_id: int | None = None,
        limit: int = 21,
    ) -> list[Build]:
        """List one page of authoritative builds in display order under a visibility policy.

        `after_id`/`before_id` anchor a keyset page relative to the display order and require the
        ID sort; `offset` skips rows instead. A `before_id` page is returned in display order, so
        an overfetched row appears at the front and must be trimmed there by the caller.
        """
        return await self._builds.list_page(
            statuses=statuses,
            submitter_id=submitter_id,
            submitter_account_id=submitter_account_id,
            sort=sort,
            offset=offset,
            after_id=after_id,
            before_id=before_id,
            limit=limit,
        )

    async def count(
        self,
        *,
        statuses: frozenset[Status],
        submitter_id: int | None = None,
        submitter_account_id: int | None = None,
    ) -> int:
        """Count the builds visible to a listing under a visibility policy."""
        return await self._builds.count(
            statuses=statuses,
            submitter_id=submitter_id,
            submitter_account_id=submitter_account_id,
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

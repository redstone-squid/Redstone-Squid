"""Application queries for builds, shared by the bot, the API, and the worker."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, get_args

from squid.builds.domain import Build, Status
from squid.builds.errors import BuildNotFoundError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page


@dataclass(frozen=True, slots=True)
class RestrictionSearchItem:
    """A restriction name or alias returned by a substring search."""

    restriction_id: int
    name: str
    is_alias: bool


type BuildSortField = Literal["id", "submission_time"]
BUILD_SORT_FIELDS: frozenset[str] = frozenset(get_args(BuildSortField.__value__))
"""Columns an authoritative build listing may order by; both are indexed."""


@dataclass(frozen=True, slots=True)
class BuildListSort:
    """Display ordering for an authoritative build listing."""

    field: BuildSortField = "id"
    descending: bool = True


DEFAULT_BUILD_LIST_SORT = BuildListSort()


def _persisted_build_id(build: Build) -> int:
    assert build.id is not None, "a listed build has always been persisted"
    return build.id


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

    async def get_public(self, build_id: int) -> Build:
        """Return a build the public catalogue may show, or raise `BuildNotFoundError`.

        One rule, one place. Four routes each wrote `build is None or
        build.submission_status is not Status.CONFIRMED`, which meant the
        definition of "public" lived in the transport layer in four copies.

        A pending build raises the same error as a missing one on purpose: the
        two are indistinguishable to a caller without
        `build.submission.view_pending`, which is what keeps a submission's
        existence private until it is confirmed.
        """
        build = await self._builds.get_by_id(build_id)
        if build is None or build.submission_status is not Status.CONFIRMED:
            raise BuildNotFoundError(build_id)
        return build

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
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        """Return one page of authoritative builds in display order under a visibility policy."""
        rows = await self._builds.list_page(
            statuses=statuses,
            submitter_id=submitter_id,
            submitter_account_id=submitter_account_id,
            sort=sort,
            offset=selector.offset,
            after_id=selector.after_id,
            before_id=selector.before_id,
            # One row past the page proves whether another page follows.
            limit=page_size + 1,
        )
        total = await self._builds.count(
            statuses=statuses,
            submitter_id=submitter_id,
            submitter_account_id=submitter_account_id,
        )
        return keyset_page(
            rows,
            selector=selector,
            page_size=page_size,
            total=total,
            keyset=sort.field == "id",
            id_of=_persisted_build_id,
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

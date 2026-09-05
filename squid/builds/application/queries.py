"""Application queries for builds, shared by the bot, the API, and the worker."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, get_args
from urllib.parse import urlsplit

from squid.builds.domain import Build, DoorBuild, Status
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import DataIntegrityError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector, keyset_page

type BuildSortField = Literal["id", "submission_time"]
BUILD_SORT_FIELDS: frozenset[str] = frozenset(get_args(BuildSortField.__value__))
"""Columns an authoritative build listing may order by; both are indexed."""


@dataclass(frozen=True, slots=True)
class BuildListSort:
    """Display ordering for an authoritative build listing."""

    field: BuildSortField = "id"
    descending: bool = True


DEFAULT_BUILD_LIST_SORT = BuildListSort()


@dataclass(frozen=True, slots=True)
class PublicBuildTag:
    """One allowlisted taxonomy value on a public build summary."""

    key: str
    name: str
    value: Decimal | str | bool | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class PublicBuildPreview:
    """The preferred HTTPS image for a public build card."""

    kind: Literal["render", "image"]
    url: str


@dataclass(frozen=True, slots=True)
class PublicBuildSummary:
    """Allowlisted build facts safe to embed in public read models."""

    id: int
    revision: int
    title: str
    display_name: str | None
    status: str
    category: str
    dimensions: tuple[int | None, int | None, int | None]
    creators: tuple[str, ...]
    tags: tuple[PublicBuildTag, ...]
    preview: PublicBuildPreview | None
    version_spec: str | None
    versions: tuple[str, ...]
    opening_time: int | None
    closing_time: int | None
    created_at: datetime | None
    updated_at: datetime | None

    @classmethod
    def from_build(cls, build: Build) -> PublicBuildSummary:
        """Project one confirmed aggregate onto its stable public fields."""
        if build.id is None:
            msg = "persisted build is missing its identifier"
            raise DataIntegrityError(msg)
        return cls(
            id=build.id,
            revision=build.revision,
            title=build.title,
            display_name=build.display_name,
            status=build.submission_status.name.casefold() if build.submission_status is not None else "unknown",
            category=build.category.value,
            dimensions=build.dimensions,
            creators=tuple(build.creators_ign),
            tags=tuple(
                PublicBuildTag(
                    key=assignment.definition.stable_key,
                    name=assignment.definition.display_name,
                    value=assignment.value,
                    unit=assignment.display_unit,
                )
                for assignment in build.tags
            ),
            preview=_public_preview(build),
            version_spec=build.version_spec,
            versions=tuple(build.versions),
            opening_time=build.normal_opening_time if isinstance(build, DoorBuild) else None,
            closing_time=build.normal_closing_time if isinstance(build, DoorBuild) else None,
            created_at=build.submission_time.to_stdlib() if build.submission_time is not None else None,
            updated_at=build.edited_time.to_stdlib() if build.edited_time is not None else None,
        )


def _public_preview(build: Build) -> PublicBuildPreview | None:
    sources: tuple[tuple[Literal["render", "image"], tuple[str, ...]], ...] = (
        ("render", build.render_urls),
        ("image", build.image_urls),
    )
    for kind, urls in sources:
        for candidate in urls:
            parsed = urlsplit(candidate)
            if parsed.scheme == "https" and parsed.netloc:
                return PublicBuildPreview(kind, candidate)
    return None


def _persisted_build_id(build: Build) -> int:
    assert build.id is not None, "a listed build has always been persisted"
    return build.id


class BuildQueryRepository(Protocol):
    """Build persistence queries required by search workflows."""

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]: ...

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]: ...

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
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
        submitter_account_id: int | None,
    ) -> int: ...

    async def get_pending(self) -> list[Build]: ...


class SemanticBuildSearch(Protocol):
    """Natural-language build lookup."""

    async def find_build_id(self, query: str) -> int | None: ...


class BuildQueryService:
    """Coordinate authoritative build reads and semantic build lookup."""

    def __init__(
        self,
        builds: BuildQueryRepository,
        semantic_search: SemanticBuildSearch,
    ):
        self._builds = builds
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

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]:
        """Return confirmed public summaries in requested order, omitting unavailable IDs."""
        return await self._builds.get_public_summaries(build_ids)

    async def list_page(
        self,
        *,
        statuses: frozenset[Status],
        submitter_account_id: int | None = None,
        sort: BuildListSort = DEFAULT_BUILD_LIST_SORT,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        """Return one page of authoritative builds in display order under a visibility policy."""
        rows = await self._builds.list_page(
            statuses=statuses,
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

    async def semantic(self, query: str) -> Build | None:
        build_id = await self._semantic_search.find_build_id(query)
        if build_id is None:
            return None
        return await self._builds.get_by_id(build_id)

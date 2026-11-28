"""Application queries for builds, shared by the bot, the API, and the worker."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import UUID

from squid.core.actors import ActingContext, WorkerActor
from squid.core.builds.domain import EMPTY_BUILD_FILTERS, BuildCategory, BuildListFilters, BuildStatus
from squid.core.builds.domain.credits import CreditAttribution, CreditKind, CreditRole
from squid.core.builds.errors import BuildNotFoundError
from squid.core.builds.history import BuildPublication, HistoryActor, PublicBuildRevision
from squid.core.builds.models import Build, DoorBuild
from squid.core.errors import DataIntegrityError
from squid.core.pagination import FIRST_PAGE, Page, PageSelector
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.domain import Pattern
from squid.core.permissions.domain.catalogue import (
    BUILD_SUBMISSION_CREATE,
    BUILD_SUBMISSION_EDIT,
    BUILD_SUBMISSION_READ,
    BUILD_SUBMISSION_VIEW_PENDING,
)
from squid.core.unit_of_work import UnitOfWork, UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class CreatorYear:
    year: int
    count: int


@dataclass(frozen=True, slots=True)
class CreatorRecord:
    id: UUID
    title: str


@dataclass(frozen=True, slots=True)
class CreatorStats:
    build_count: int
    builds_by_year: tuple[CreatorYear, ...]
    records_held: tuple[CreatorRecord, ...]
    records_ever: int


class BuildListSort(StrEnum):
    """A named display order for build listings.

    Each order is total, so cursors work with all of them. `ID` lists the newest IDs first and
    never moves a row, which makes it the order for full traversal.
    """

    ID = "id"
    NEWEST = "newest"
    OLDEST = "oldest"
    UPDATED = "updated"
    FASTEST_OPEN = "fastest_open"
    SMALLEST_VOLUME = "smallest_volume"
    TITLE = "title"


@dataclass(frozen=True, slots=True)
class PublicBuildTag:
    """One allowlisted tag value on a public build summary."""

    key: str
    name: str
    value: Decimal | str | bool | None
    unit: str | None


@dataclass(frozen=True, slots=True)
class PublicBuildPreview:
    """The preferred HTTPS image for a public build card."""

    kind: Literal["render", "image"]
    url: str
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True, slots=True)
class PublicBuildDimensions:
    """A three-dimensional measurement in blocks; None means that dimension is unknown."""

    width: int | None
    height: int | None
    depth: int | None


@dataclass(frozen=True, slots=True)
class BuildRecordSummary:
    held_count: int = 0
    former_count: int = 0


@dataclass(frozen=True, slots=True)
class PublicBuildCreator:
    name: str
    account_id: UUID | None
    """The public ID of the account credited under this name, if any."""


@dataclass(frozen=True, slots=True)
class PublicBuildCredit:
    """Public attribution without private account links or provider identifiers."""

    name: str
    kind: CreditKind
    username: str | None
    role: CreditRole
    attribution: CreditAttribution
    account_id: UUID | None
    credit_id: UUID | None = None
    name_id: int | None = None


class PublicBuildStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PublicBuildSummary:
    """Build fields exposed by public catalogue queries.

    The REST API serializes this type as it is, so every field here is public
    and a new field changes the published contract.
    """

    id: int
    revision: int
    title: str
    display_name: str | None
    status: PublicBuildStatus
    category: BuildCategory
    dimensions: PublicBuildDimensions
    creators: tuple[PublicBuildCreator, ...]
    tags: tuple[PublicBuildTag, ...]
    preview: PublicBuildPreview | None
    version_spec: str | None
    versions: tuple[str, ...]
    opening_time: int | None
    closing_time: int | None
    created_at: datetime | None
    updated_at: datetime | None
    record_summary: BuildRecordSummary = BuildRecordSummary()
    subtitle: str | None = None
    credits: tuple[PublicBuildCredit, ...] = ()

    @classmethod
    def from_build(cls, build: Build) -> PublicBuildSummary:
        """Copy display fields from a persisted build.

        The caller must check visibility first; this method does not require the
        build to be approved.
        """
        # ORM identity columns may still be None before flush.
        if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
            msg = "persisted build is missing its identifier"
            raise DataIntegrityError(msg)
        return cls(
            id=build.id,
            revision=build.revision,
            title=build.title,
            subtitle=build.subtitle,
            record_summary=BuildRecordSummary(len(build.published_records.held), len(build.published_records.former)),
            display_name=build.display_name,
            status=PublicBuildStatus[build.submission_status.name]
            if build.submission_status is not None
            else PublicBuildStatus.UNKNOWN,
            category=build.category,
            dimensions=PublicBuildDimensions(width=build.width, height=build.height, depth=build.depth),
            creators=tuple(
                dict.fromkeys(
                    PublicBuildCreator(credit.name, credit.account_id)
                    for credit in build.credits
                    if credit.role in {CreditRole.CREATOR, CreditRole.DESIGNER}
                )
            ),
            credits=tuple(
                PublicBuildCredit(
                    name=credit.name,
                    credit_id=credit.credit_id,
                    name_id=credit.name_id,
                    account_id=credit.account_id,
                    kind=credit.kind,
                    username=credit.username,
                    role=credit.role,
                    attribution=credit.attribution,
                )
                for credit in build.credits
            ),
            tags=tuple(
                PublicBuildTag(
                    key=assignment.definition.stable_key,
                    name=assignment.definition.display_name,
                    value=assignment.presentation_value,
                    unit=assignment.presentation_unit.key if assignment.presentation_unit else None,
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


class BuildQueryRepository(Protocol):
    """Build persistence queries required by search workflows."""

    async def creator_stats(self, filters: BuildListFilters) -> CreatorStats: ...

    async def get_by_id(self, build_id: int) -> Build | None: ...

    async def get_many(self, build_ids: Sequence[int]) -> list[Build]: ...

    async def get_public_summaries(self, build_ids: Sequence[int]) -> Sequence[PublicBuildSummary]: ...

    async def list_page(
        self,
        *,
        statuses: frozenset[BuildStatus],
        filters: BuildListFilters,
        submitter_account_id: int | None,
        sort: BuildListSort,
        selector: PageSelector,
        page_size: int,
    ) -> Page[Build]: ...

    async def count(
        self,
        *,
        statuses: frozenset[BuildStatus],
        filters: BuildListFilters,
        submitter_account_id: int | None,
    ) -> int: ...

    async def get_pending(self) -> list[Build]: ...


class SemanticBuildSearch(Protocol):
    """Natural-language build lookup."""

    async def find_build_id(self, query: str, *, context: ActingContext) -> int | None: ...


async def visible_builds(
    builds: Sequence[Build],
    context: ActingContext,
    permissions: PermissionService | None,
    *,
    editing: bool = False,
    uow: PermissionUnitOfWork | None = None,
) -> Sequence[Build]:
    """Include private builds for the submitter, permitted staff, and background processors."""
    actor = context.effective
    if isinstance(actor, WorkerActor):
        return builds
    owner_id = actor.linked_account_id
    if owner_id is not None and actor.credential_limit is not None:
        owner_nodes = (BUILD_SUBMISSION_READ, BUILD_SUBMISSION_CREATE) if editing else (BUILD_SUBMISSION_READ,)
        if not any(Pattern.parse(raw).matches(node) for raw in actor.credential_limit for node in owner_nodes):
            owner_id = None
    visible = [
        build
        for build in builds
        if build.submission_status is BuildStatus.APPROVED
        or (owner_id is not None and build.submitter_account_id == owner_id)
    ]
    if len(visible) == len(builds) or permissions is None:
        return visible
    nodes = (BUILD_SUBMISSION_VIEW_PENDING, BUILD_SUBMISSION_EDIT) if editing else (BUILD_SUBMISSION_VIEW_PENDING,)
    if (await permissions.check(actor, *nodes, mode="any", uow=uow)).allowed:
        return builds
    return visible


class BuildQueryService:
    """Read stored builds by ID, listing filters, or a natural-language query."""

    def __init__(
        self,
        semantic_search: SemanticBuildSearch,
        permissions: PermissionService,
        *,
        uow_factory: UnitOfWorkFactory[BuildQueryUnitOfWork],
    ):
        self._uow_factory = uow_factory
        self._semantic_search = semantic_search
        self._permissions = permissions

    async def get(self, build_id: int, *, context: ActingContext) -> Build | None:
        rows = await self.get_many((build_id,), context=context)
        return rows[0] if rows else None

    async def creator_stats(self, filters: BuildListFilters, *, context: ActingContext) -> CreatorStats:
        """Summarize the approved builds in a creator's catalogue listing."""
        with context.scope():
            async with self._uow_factory() as uow:
                return await uow.builds.creator_stats(filters)

    async def get_public(self, build_id: int, *, context: ActingContext) -> Build:
        """Return a approved build, or raise BuildNotFoundError.

        Missing, pending, and denied builds produce the same error so this public
        lookup does not reveal builds awaiting review.
        """
        with context.scope():
            async with self._uow_factory() as uow:
                build = await uow.builds.get_by_id(build_id)
                if build is None or build.submission_status is not BuildStatus.APPROVED:
                    raise BuildNotFoundError(build_id)
                return build

    async def publication(self, build: Build, *, context: ActingContext) -> BuildPublication:
        with context.scope():
            async with self._uow_factory() as uow:
                return await uow.build_history.publication(build)

    async def submitters(self, builds: Sequence[Build], *, context: ActingContext) -> dict[int, HistoryActor]:
        """Map build IDs to their submitters' public names; the caller must already see the builds."""
        with context.scope():
            async with self._uow_factory() as uow:
                return await uow.build_history.submitters(builds)

    async def public_history(
        self, build_id: int, labels: Mapping[str, str], *, context: ActingContext, offset: int, page_size: int
    ) -> Page[PublicBuildRevision]:
        with context.scope():
            async with self._uow_factory() as uow:
                build = await uow.builds.get_by_id(build_id)
                if build is None or build.submission_status is not BuildStatus.APPROVED:
                    raise BuildNotFoundError(build_id)
                return await uow.build_history.public_page(build_id, labels, offset=offset, page_size=page_size)

    async def pending(self, *, context: ActingContext) -> Sequence[Build]:
        with context.scope():
            async with self._uow_factory() as uow:
                if not isinstance(context.effective, WorkerActor):
                    await self._permissions.require(context.effective, BUILD_SUBMISSION_VIEW_PENDING)
                return await uow.builds.get_pending()

    async def get_many(self, build_ids: Sequence[int], *, context: ActingContext) -> list[Build]:
        """Load builds in the caller's requested order, omitting missing rows."""
        with context.scope():
            async with self._uow_factory() as uow:
                rows = await uow.builds.get_many(build_ids)
            return list(await visible_builds(rows, context, self._permissions))

    async def get_public_summaries(
        self, build_ids: Sequence[int], *, context: ActingContext
    ) -> Sequence[PublicBuildSummary]:
        """Return confirmed public summaries in requested order, omitting unavailable IDs."""
        with context.scope():
            async with self._uow_factory() as uow:
                return await uow.builds.get_public_summaries(build_ids)

    async def list_page(
        self,
        *,
        context: ActingContext,
        statuses: frozenset[BuildStatus],
        filters: BuildListFilters = EMPTY_BUILD_FILTERS,
        submitter_account_id: int | None = None,
        sort: BuildListSort = BuildListSort.ID,
        selector: PageSelector = FIRST_PAGE,
        page_size: int = 20,
    ) -> Page[Build]:
        """Return a page filtered by the supplied statuses and optional submitter.

        Unpublished listings require review access unless restricted to the caller's own submissions.
        """
        with context.scope():
            actor = context.effective
            own_submissions = actor.linked_account_id is not None and submitter_account_id == actor.linked_account_id
            if statuses != {BuildStatus.APPROVED} and not isinstance(actor, WorkerActor) and not own_submissions:
                await self._permissions.require(actor, BUILD_SUBMISSION_VIEW_PENDING)
            async with self._uow_factory() as uow:
                return await uow.builds.list_page(
                    statuses=statuses,
                    filters=filters,
                    submitter_account_id=submitter_account_id,
                    sort=sort,
                    selector=selector,
                    page_size=page_size,
                )

    async def semantic(self, query: str, *, context: ActingContext) -> Build | None:
        """Return the nearest indexed build, or None if no match can be loaded."""
        with context.scope():
            build_id = await self._semantic_search.find_build_id(query, context=context)
            if build_id is None:
                return None
            return await self.get(build_id, context=context)


class PublicHistoryReader(Protocol):
    async def publication(self, build: Build) -> BuildPublication: ...

    async def submitters(self, builds: Sequence[Build]) -> dict[int, HistoryActor]: ...

    async def public_page(
        self, build_id: int, labels: Mapping[str, str], *, offset: int, page_size: int
    ) -> Page[PublicBuildRevision]: ...


class BuildQueryUnitOfWork(UnitOfWork, Protocol):
    @property
    def build_history(self) -> PublicHistoryReader: ...

    @property
    def builds(self) -> BuildQueryRepository: ...

"""Application services for build submission and editing."""

from collections.abc import Sequence
from uuid import UUID

from whenever import Instant

from squid.builds.application.commands import DoorSubmissionInput
from squid.builds.application.editing import BuildEditLease, BuildEditPatch
from squid.builds.application.ports import (
    BuildEmbeddingCoordinator,
    BuildLockManager,
    BuildRepository,
    DefaultVersionResolver,
)
from squid.builds.application.restrictions import RestrictionRepository
from squid.builds.domain import Build, BuildCategory, Status
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import InvalidStateError


class BuildService:
    """Framework-free application operations for builds."""

    def __init__(
        self,
        repository: BuildRepository,
        locks: BuildLockManager,
        restrictions: RestrictionRepository,
        versions: DefaultVersionResolver,
        embeddings: BuildEmbeddingCoordinator,
    ) -> None:
        self._repository = repository
        self._locks = locks
        self._restrictions = restrictions
        self._versions = versions
        self._embeddings = embeddings

    async def get(self, build_id: int) -> Build | None:
        return await self._repository.get_by_id(build_id)

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        """Return the build previously finalized from a synchronized draft, if any."""
        return await self._repository.get_by_source_submission_draft_id(draft_id)

    async def submit_door(self, submission: DoorSubmissionInput) -> Build:
        build = Build(
            submitter_id=submission.submitter_id,
            ai_generated=submission.ai_generated,
            category=BuildCategory.DOOR,
            submission_status=Status.PENDING,
            version_spec=submission.works_in,
            width=submission.build_size[0],
            height=submission.build_size[1],
            depth=submission.build_size[2],
            door_width=submission.door_size[0],
            door_height=submission.door_size[1],
            door_depth=submission.door_size[2],
            door_type=list(submission.pattern),
            door_orientation_type=submission.door_type,
            normal_closing_time=submission.normal_closing_time,
            normal_opening_time=submission.normal_opening_time,
            creators_ign=list(submission.creators),
            image_urls=list(submission.image_urls),
            video_urls=list(submission.video_urls),
            world_download_urls=list(submission.world_download_urls),
            schematic_urls=list(submission.schematic_urls),
            completion_time=submission.date_of_creation,
        )
        await self._set_restrictions(build, submission.restrictions)
        for value, empty_value in (
            (submission.locationality, "Not locational"),
            (submission.directionality, "Not directional"),
        ):
            if value is not None and value != empty_value:
                build.miscellaneous_restrictions.append(value)
        if submission.information_about_build is not None:
            build.extra_info["user"] = submission.information_about_build
            build.description = submission.information_about_build
        await self._persist(build)
        return build

    async def save(self, build: Build) -> Build:
        await self._persist(build)
        return build

    async def clean_stale_locks(self, *, older_than: Instant) -> None:
        """Release persisted build locks older than a cutoff."""
        await self._locks.clean_stale(older_than=older_than)

    async def classify_restrictions(self, build: Build, restrictions: Sequence[str]) -> Build:
        """Replace a build's restrictions using repository-owned metadata."""
        definitions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(restrictions, {definition.name: definition.type for definition in definitions})
        return build

    async def submit(
        self,
        build: Build,
        *,
        submitter_id: int,
        ai_generated: bool,
        category: BuildCategory = BuildCategory.DOOR,
    ) -> Build:
        """Apply legacy Discord submission metadata and persist an already prepared build."""
        build.submitter_account_id = None
        build.submitter_id = submitter_id
        build.ai_generated = ai_generated
        build.category = category
        build.submission_status = Status.PENDING
        await self._persist(build)
        return build

    async def submit_for_account(
        self,
        build: Build,
        *,
        submitter_account_id: int,
        source_submission_draft_id: UUID,
        display_name: str | None,
        ai_generated: bool,
        category: BuildCategory,
    ) -> Build:
        """Finalize one synchronized draft under a provider-neutral account.

        The draft UUID is both persisted for audit and used as the retry key. A later
        finalization attempt returns the already-created build without requiring any
        Discord identity on the owning account.
        """
        existing = await self._repository.get_by_source_submission_draft_id(source_submission_draft_id)
        if existing is not None:
            if existing.submitter_account_id != submitter_account_id:
                msg = "The source submission draft is already owned by another account."
                raise InvalidStateError(
                    msg,
                    context={"source_submission_draft_id": str(source_submission_draft_id)},
                )
            if existing.sponsor != build.sponsor:
                msg = "The source submission draft already produced a build with different immutable provenance."
                raise InvalidStateError(
                    msg,
                    context={"source_submission_draft_id": str(source_submission_draft_id)},
                )
            return existing
        build.submitter_account_id = submitter_account_id
        build.submitter_id = None
        build.source_submission_draft_id = source_submission_draft_id
        build.display_name = display_name.strip() if display_name is not None and display_name.strip() else None
        build.ai_generated = ai_generated
        build.category = category
        build.submission_status = Status.PENDING
        await self._persist(build)
        return build

    def edit(
        self,
        build_id: int,
        patch: BuildEditPatch,
        *,
        blocking: bool = False,
        timeout: float = 30,
        expected_revision: int | None = None,
    ) -> BuildEditLease:
        return BuildEditLease(
            self._repository,
            self._locks,
            self._persist_without_lock,
            build_id,
            patch,
            blocking=blocking,
            timeout=timeout,
            expected_revision=expected_revision,
        )

    async def confirm(self, build_id: int) -> Build:
        async with self._locks.locked(build_id):
            build = await self._get_required(build_id)
            await self._repository.confirm(build)
        return build

    async def deny(self, build_id: int) -> Build:
        async with self._locks.locked(build_id):
            build = await self._get_required(build_id)
            await self._repository.deny(build)
        return build

    async def _get_required(self, build_id: int) -> Build:
        build = await self._repository.get_by_id(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        return build

    async def _persist(self, build: Build) -> None:
        if build.id is None:
            await self._persist_without_lock(build)
            return
        async with self._locks.locked(build.id):
            await self._persist_without_lock(build)

    async def _persist_without_lock(self, build: Build) -> None:
        """Persist a build when the caller already owns any required edit lease."""
        if not build.versions:
            build.versions = [await self._versions.newest("Java")]
        await self._embeddings.prepare(build)
        await self._repository.save(build)
        await self._embeddings.index(build)

    async def _set_restrictions(self, build: Build, restrictions: Sequence[str]) -> None:
        known_restrictions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(
            restrictions,
            {restriction.name: restriction.type for restriction in known_restrictions},
        )

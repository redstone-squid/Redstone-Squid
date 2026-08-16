"""Application services for build submission and editing."""

from collections.abc import Sequence
from dataclasses import dataclass
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
from squid.builds.application.taxonomy import BuildTaxonomyResolver, apply_build_taxonomy
from squid.builds.domain import Build, BuildDraft, BuildLink, DoorBuild, Status
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT


@dataclass(frozen=True, slots=True)
class BuildEditor:
    """Who is editing a build, in the terms the edit policy asks about.

    Two facts and no transport: the permission subject behind the caller, and the
    Discord id ownership is recorded against. An HTTP request, a slash command,
    and a modal submission all reduce to this.
    """

    subject: Subject
    discord_id: int | None = None


class BuildService:
    """Framework-free application operations for builds."""

    def __init__(
        self,
        repository: BuildRepository,
        locks: BuildLockManager,
        restrictions: RestrictionRepository,
        versions: DefaultVersionResolver,
        embeddings: BuildEmbeddingCoordinator,
        taxonomy: BuildTaxonomyResolver,
        *,
        permissions: PermissionService | None = None,
    ) -> None:
        self._repository = repository
        self._locks = locks
        self._restrictions = restrictions
        self._versions = versions
        self._embeddings = embeddings
        self._taxonomy = taxonomy
        self._permissions = permissions

    async def get(self, build_id: int) -> Build | None:
        return await self._repository.get_by_id(build_id)

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        """Return the build previously finalized from a synchronized draft, if any."""
        return await self._repository.get_by_source_submission_draft_id(draft_id)

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        """Return every build inferred from one Discord message, newest bundle included."""
        return await self._repository.list_ids_for_source_message(message_id)

    async def submit_door(self, submission: DoorSubmissionInput) -> DoorBuild:
        build = DoorBuild(
            submitter_id=submission.submitter_id,
            ai_generated=submission.ai_generated,
            submission_status=Status.PENDING,
            version_spec=submission.works_in,
            width=submission.build_size[0],
            height=submission.build_size[1],
            depth=submission.build_size[2],
            door_width=submission.door_size[0] if submission.door_size[0] is not None else 1,
            door_height=submission.door_size[1] if submission.door_size[1] is not None else 2,
            door_depth=submission.door_size[2],
            patterns=list(submission.pattern),
            orientation=submission.door_type,
            normal_closing_time=submission.normal_closing_time,
            normal_opening_time=submission.normal_opening_time,
            creators_ign=list(submission.creators),
            links=[
                BuildLink(url=url, media_type=media_type)
                for media_type, urls in (
                    ("image", submission.image_urls),
                    ("video", submission.video_urls),
                    ("world-download", submission.world_download_urls),
                    ("schematic", submission.schematic_urls),
                )
                for url in urls
            ],
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

    async def classify_restrictions[BuildT: (Build, BuildDraft)](
        self, build: BuildT, restrictions: Sequence[str]
    ) -> BuildT:
        """Replace a build's restrictions using repository-owned metadata."""
        definitions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(restrictions, {definition.name: definition.type for definition in definitions})
        return build

    async def submit(self, build: Build, *, submitter_id: int, ai_generated: bool) -> Build:
        """Apply legacy Discord submission metadata and persist an already prepared build.

        The build's category is a fact of its type; callers construct the right
        subclass (or finalize a :class:`BuildDraft`) before submitting.
        """
        build.submitter_account_id = None
        build.submitter_id = submitter_id
        build.ai_generated = ai_generated
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

    async def apply_edit(
        self,
        actor: BuildEditor,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
    ) -> Build:
        """Edit an owned pending build, or any build with `build.submission.edit`.

        The authorizing wrapper around `edit()`, not a replacement for it. This
        policy used to live in the HTTP route, which read the leased build's
        status and submitter and decided there -- so the bot's two edit paths
        could not reuse it, and the rule existed only for HTTP callers.

        Authorization happens inside the lease because it reads the build: a
        check before the load would race an approval that flips the build out of
        `PENDING` between the two.
        """
        if self._permissions is None:
            msg = "Authorized editing requires a permission service."
            raise InvalidStateError(msg)
        async with self.edit(build_id, patch, blocking=False, expected_revision=expected_revision) as lease:
            owns = (
                lease.build.submission_status is Status.PENDING
                and actor.discord_id is not None
                and lease.build.submitter_id == actor.discord_id
            )
            if not owns and not await self._permissions.allows(actor.subject, BUILD_SUBMISSION_EDIT):
                raise AuthorizationError
            return await lease.commit()

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
        # Resolve the editable taxonomy strings into tag assignments here, at the
        # last application-owned step, so the repository persists build.tags
        # verbatim and unresolvable names are recorded before anything is saved.
        await apply_build_taxonomy(build, self._taxonomy)
        await self._embeddings.prepare(build)
        await self._repository.save(build)
        await self._embeddings.index(build)

    async def _set_restrictions(self, build: Build, restrictions: Sequence[str]) -> None:
        known_restrictions = await self._restrictions.fetch_all_restrictions()
        build.classify_restrictions(
            restrictions,
            {restriction.name: restriction.type for restriction in known_restrictions},
        )

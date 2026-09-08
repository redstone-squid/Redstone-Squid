"""Application services for build submission and editing."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from whenever import Instant

from squid.builds.application.editing import BuildEditLease, BuildEditPatch
from squid.builds.application.ports import (
    BuildEmbeddingCoordinator,
    BuildLockManager,
    BuildRepository,
    DefaultVersionResolver,
)
from squid.builds.application.restrictions import RestrictionRepository
from squid.builds.application.taxonomy import BuildTaxonomyResolver, apply_build_taxonomy
from squid.builds.domain import (
    Build,
    BuildDraft,
    RestrictionTypeLiteral,
    Status,
    sort_restrictions,
)
from squid.builds.errors import BuildNotFoundError
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Subject
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT


@dataclass(frozen=True, slots=True)
class BuildEditor:
    """Who is editing a build, in the terms the edit policy asks about.

    One fact and no transport: the permission subject behind the caller, which already
    carries the account ownership is recorded against. An HTTP request, a slash command,
    and a modal submission all reduce to this.
    """

    subject: Subject


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

    async def sort_restrictions(self, restrictions: Sequence[str]) -> dict[RestrictionTypeLiteral, list[str]]:
        """Group restriction names by bucket without writing them onto a build."""
        definitions = await self._restrictions.fetch_all_restrictions()
        return sort_restrictions(restrictions, {definition.name: definition.type for definition in definitions})

    async def prepare_for_account(
        self,
        build: Build,
        *,
        submitter_account_id: int,
        source_submission_draft_id: UUID,
        display_name: str | None,
        ai_generated: bool,
    ) -> Build:
        """Resolve submission metadata and derived values before the caller's transaction."""
        build.submitter_account_id = submitter_account_id
        build.source_submission_draft_id = source_submission_draft_id
        build.display_name = display_name.strip() if display_name is not None and display_name.strip() else None
        build.ai_generated = ai_generated
        build.submission_status = Status.PENDING
        await self._prepare_for_persistence(build)
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
        async with self.edit(build_id, patch, blocking=False, expected_revision=expected_revision) as lease:
            await self.authorize_edit(actor, lease.build)
            return await lease.commit()

    async def authorize_edit(self, actor: BuildEditor, build: Build) -> None:
        """Apply the shared live edit policy to the caller's current build snapshot."""
        if self._permissions is None:
            msg = "Authorized editing requires a permission service."
            raise InvalidStateError(msg)
        owns = (
            build.submission_status is Status.PENDING
            and actor.subject.account_id is not None
            and build.submitter_account_id == actor.subject.account_id
        )
        if not owns and not await self._permissions.allows(actor.subject, BUILD_SUBMISSION_EDIT):
            raise AuthorizationError

    async def prepare_edit(self, actor: BuildEditor, build: Build, patch: BuildEditPatch) -> Build:
        """Resolve an authorized edit before its caller's revision-fenced transaction."""
        await self.authorize_edit(actor, build)
        patch.apply(build)
        await self._prepare_for_persistence(build)
        return build

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
        await self._prepare_for_persistence(build)
        await self._repository.save(build)
        await self._embeddings.index(build)

    async def _prepare_for_persistence(self, build: Build) -> None:
        """Resolve application-owned defaults and derived values before a relational write."""
        if not build.versions:
            build.versions = [await self._versions.newest("Java")]
        # Resolve the editable taxonomy strings into tag assignments here, at the
        # last application-owned step, so the repository persists build.tags
        # verbatim and unresolvable names are recorded before anything is saved.
        await apply_build_taxonomy(build, self._taxonomy)
        await self._embeddings.prepare(build)

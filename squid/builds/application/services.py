"""Application services for build submission and editing."""

from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from whenever import Instant

from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.ports import (
    BuildEmbeddingCoordinator,
    BuildLockManager,
    BuildUnitOfWork,
    DefaultVersionResolver,
)
from squid.builds.application.restrictions import RestrictionReader
from squid.builds.application.taxonomy import BuildTaxonomyResolver, apply_build_taxonomy
from squid.builds.domain import BuildDraft, RestrictionTypeLiteral, Status, sort_restrictions
from squid.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.builds.history import BuildEditActor, BuildEditContext, HistoryOperation
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.unit_of_work import UnitOfWorkFactory


@dataclass(frozen=True, slots=True)
class BuildEditor:
    """The permission subject requesting a build edit."""

    subject: Actor
    source: str = "build_edit"


class BuildService:
    """Save new builds and apply edits to existing builds.

    Preparation resolves taxonomy names, fills default versions, and generates
    an embedding before persistence. save and edit serve trusted callers;
    apply_edit checks the requesting account's permission.

    Edits prepare detached values, including embeddings, before acquiring a
    build lease or row lock. Concurrent edits can prepare together; saving
    reloads the build and rejects a changed revision. Authorized edits also
    recheck the actor's permissions inside the write transaction.
    """

    def __init__(
        self,
        locks: BuildLockManager,
        restrictions: RestrictionReader,
        versions: DefaultVersionResolver,
        embeddings: BuildEmbeddingCoordinator,
        taxonomy: BuildTaxonomyResolver,
        *,
        uow_factory: UnitOfWorkFactory[BuildUnitOfWork],
        permissions: PermissionService | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._locks = locks
        self._restrictions = restrictions
        self._versions = versions
        self._embeddings = embeddings
        self._taxonomy = taxonomy
        self._permissions = permissions

    async def get(self, build_id: int) -> Build | None:
        async with self._uow_factory() as uow:
            return await uow.builds.get_by_id(build_id)

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        """Return the build previously finalized from a synchronized draft, if any."""
        async with self._uow_factory() as uow:
            return await uow.builds.get_by_source_submission_draft_id(draft_id)

    async def list_ids_for_source_message(self, message_id: int) -> Sequence[int]:
        """Return IDs of builds linked to a Discord source message, in ascending order."""
        async with self._uow_factory() as uow:
            return await uow.builds.list_ids_for_source_message(message_id)

    async def save(
        self,
        build: Build,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> Build:
        """Prepare and persist a build, then update its embedding index.

        This trusted entry point does not check permissions. Indexing happens after
        the database commit, so an indexing failure does not undo the saved build.
        """
        context = BuildEditContext.from_actor(
            actor, source=source, operation=operation, correlation_kind=correlation_kind, correlation_id=correlation_id
        )
        await self._persist(build, context=context)
        return build

    async def clean_stale_locks(self, *, older_than: Instant) -> None:
        """Ask the lock manager to reclaim expired leases."""
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

    async def edit(
        self,
        build_id: int,
        patch: BuildEditPatch,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "edit",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
        expected_revision: int | None = None,
    ) -> Build:
        """Apply a trusted caller's patch using a short, revision-checked write."""
        context = BuildEditContext.from_actor(
            actor, source=source, operation=operation, correlation_kind=correlation_kind, correlation_id=correlation_id
        )
        build = await self._prepare_edit(build_id, patch, expected_revision=expected_revision)
        await self._persist_prepared(build, context=context)
        return build

    async def apply_edit(
        self,
        actor: BuildEditor,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
    ) -> Build:
        """Prepare an edit, then recheck current authority and revision before writing."""
        if actor.subject.account_id is None:
            raise AuthorizationError
        build = await self._get_required(build_id)
        await self.authorize_edit(actor, build)
        self._check_revision(build, expected_revision)
        patch.apply(build)
        await self._prepare_for_persistence(build)
        await self._persist_prepared(
            build,
            actor=actor,
            context=BuildEditContext(operation="edit", source=actor.source, actor_account_id=actor.subject.account_id),
        )
        return build

    async def _prepare_edit(self, build_id: int, patch: BuildEditPatch, *, expected_revision: int | None) -> Build:
        build = await self._get_required(build_id)
        self._check_revision(build, expected_revision)
        patch.apply(build)
        await self._prepare_for_persistence(build)
        return build

    @staticmethod
    def _check_revision(build: Build, expected_revision: int | None) -> None:
        if expected_revision is not None and build.revision != expected_revision:
            assert build.id is not None
            raise BuildRevisionMismatchError(
                build.id, expected_revision=expected_revision, current_revision=build.revision
            )

    async def authorize_edit(self, actor: BuildEditor, build: Build) -> None:
        """Require edit permission unless the actor owns this pending submission."""
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

    async def authorize_edit_in(self, uow: BuildUnitOfWork, actor: BuildEditor, build: Build) -> None:
        """Check edit permission using the caller's transaction and current build state."""
        if self._permissions is None:
            msg = "Authorized editing requires a permission service."
            raise InvalidStateError(msg)
        owns = (
            build.submission_status is Status.PENDING
            and actor.subject.account_id is not None
            and build.submitter_account_id == actor.subject.account_id
        )
        if not owns:
            capabilities = await self._permissions.capabilities_in(uow, actor.subject, (BUILD_SUBMISSION_EDIT,))
            if BUILD_SUBMISSION_EDIT.name not in capabilities:
                raise AuthorizationError

    async def prepare_edit(self, actor: BuildEditor, build: Build, patch: BuildEditPatch) -> Build:
        """Apply an authorized patch and prepare derived values without saving.

        The caller must recheck permissions and the revision when it later writes
        the build; this preparation does not reserve the current revision.
        """
        await self.authorize_edit(actor, build)
        patch.apply(build)
        await self._prepare_for_persistence(build)
        return build

    async def confirm(
        self,
        build_id: int,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "moderation",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> Build:
        """Confirm a build under its lease and commit the new status."""
        context = BuildEditContext.from_actor(
            actor, source=source, operation=operation, correlation_kind=correlation_kind, correlation_id=correlation_id
        )
        async with (
            self._locks.locked(build_id),
            self._uow_factory() as uow,
            uow.builds.editing(
                actor=context.actor,
                source=context.source,
                operation=context.operation,
                correlation_kind=context.correlation_kind,
                correlation_id=context.correlation_id,
            ),
        ):
            build = await self._get_for_update(uow, build_id)
            await uow.builds.confirm(build)
            await uow.commit()
        return build

    async def deny(
        self,
        build_id: int,
        *,
        actor: BuildEditActor,
        source: str,
        operation: HistoryOperation = "moderation",
        correlation_kind: str | None = None,
        correlation_id: str | None = None,
    ) -> Build:
        """Deny a build under its lease and commit the new status."""
        context = BuildEditContext.from_actor(
            actor, source=source, operation=operation, correlation_kind=correlation_kind, correlation_id=correlation_id
        )
        async with (
            self._locks.locked(build_id),
            self._uow_factory() as uow,
            uow.builds.editing(
                actor=context.actor,
                source=context.source,
                operation=context.operation,
                correlation_kind=context.correlation_kind,
                correlation_id=context.correlation_id,
            ),
        ):
            build = await self._get_for_update(uow, build_id)
            await uow.builds.deny(build)
            await uow.commit()
        return build

    async def _get_required(self, build_id: int) -> Build:
        build = await self.get(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        return build

    @staticmethod
    async def _get_for_update(uow: BuildUnitOfWork, build_id: int) -> Build:
        build = await uow.builds.get_for_update(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        return build

    async def _persist(self, build: Build, *, context: BuildEditContext) -> None:
        await self._prepare_for_persistence(build)
        await self._persist_prepared(build, context=context)

    async def _persist_prepared(
        self, build: Build, *, context: BuildEditContext, actor: BuildEditor | None = None
    ) -> None:
        """Save prepared values within a short write scope.

        For existing builds, the application lease covers this scope, and
        get_for_update holds a database row lock until commit. Embedding index
        updates run after both locks have been released.
        """
        if build.id is None:
            async with (
                self._uow_factory() as uow,
                uow.builds.editing(
                    actor=context.actor,
                    source=context.source,
                    operation=context.operation,
                    correlation_kind=context.correlation_kind,
                    correlation_id=context.correlation_id,
                ),
            ):
                await uow.builds.save(build)
                await uow.commit()
        else:
            async with (
                self._locks.locked(build.id),
                self._uow_factory() as uow,
                uow.builds.editing(
                    actor=context.actor,
                    source=context.source,
                    operation=context.operation,
                    correlation_kind=context.correlation_kind,
                    correlation_id=context.correlation_id,
                ),
            ):
                current = await self._get_for_update(uow, build.id)
                self._check_revision(current, build.revision)
                if actor is not None:
                    await self.authorize_edit_in(uow, actor, current)
                await uow.builds.save(build)
                await uow.commit()
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

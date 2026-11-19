"""Application services for build submission and editing."""

from collections.abc import Sequence
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
from squid.builds.application.taxonomy import BuildTaxonomyResolver, apply_build_taxonomy, replace_showcase_tags
from squid.builds.domain import BuildDraft, EditAccess, RestrictionTypeLiteral, Status, sort_restrictions
from squid.builds.errors import BuildNotFoundError, BuildRevisionMismatchError
from squid.builds.history import BuildEditActor, BuildEditContext, HistoryOperation
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import AccountRequiredError, InvalidStateError
from squid.core.sentinel import UNSET
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_EDIT
from squid.unit_of_work import UnitOfWorkFactory


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

    async def next_attachment_review(self, after: int) -> int | None:
        async with self._uow_factory() as uow:
            return await uow.builds.next_attachment_review(after)

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
        actor: Actor,
        build_id: int,
        patch: BuildEditPatch,
        *,
        expected_revision: int | None = None,
        source: str = "build_edit",
    ) -> Build:
        """Prepare an edit, then recheck current authority and revision before writing."""
        if actor.account_id is None:
            raise AccountRequiredError
        build = await self._get_required(build_id)
        await self.authorize_edit(actor, build)
        self._check_revision(build, expected_revision)
        await self._apply_patch(build, patch)
        await self._persist_prepared(
            build,
            actor=actor,
            context=BuildEditContext(operation="edit", source=source, actor_account_id=actor.account_id),
        )
        return build

    async def _prepare_edit(self, build_id: int, patch: BuildEditPatch, *, expected_revision: int | None) -> Build:
        build = await self._get_required(build_id)
        self._check_revision(build, expected_revision)
        await self._apply_patch(build, patch)
        return build

    @staticmethod
    def _check_revision(build: Build, expected_revision: int | None) -> None:
        if expected_revision is not None and build.revision != expected_revision:
            assert build.id is not None
            raise BuildRevisionMismatchError(
                build.id, expected_revision=expected_revision, current_revision=build.revision
            )

    async def authorize_edit(
        self, actor: Actor, build: Build, *, uow: BuildUnitOfWork | None = None, showcase_only: bool = False
    ) -> None:
        """Require the access `Build.edit_access` grants for this edit.

        Pass `showcase_only` when the edit changes nothing but showcase tags, and `uow` to check
        within the caller's transaction.
        """
        if self._permissions is None:
            msg = "Authorized editing requires a permission service."
            raise InvalidStateError(msg)
        may_edit_any = (await self._permissions.check(actor, BUILD_SUBMISSION_EDIT, uow=uow)).allowed
        access = build.edit_access(actor.account_id, may_edit_any=may_edit_any)
        if access is EditAccess.FULL or (showcase_only and access is EditAccess.SHOWCASE_TAGS):
            return
        # Raise the shared permission denial, which explains which rule refused the node.
        await self._permissions.require(actor, BUILD_SUBMISSION_EDIT, uow=uow)

    async def prepare_edit(
        self, actor: Actor, build: Build, patch: BuildEditPatch, *, showcase_only: bool = False
    ) -> Build:
        """Apply an authorized patch and prepare derived values without saving.

        The caller must recheck permissions and the revision when it later writes
        the build; this preparation does not reserve the current revision.
        """
        await self.authorize_edit(actor, build, showcase_only=showcase_only)
        await self._apply_patch(build, patch)
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
        """Apply a vote approval under the build lease, ignoring builds already reviewed."""
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
            if build.submission_status is not Status.PENDING:
                return build
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
        """Apply a vote rejection under the build lease, ignoring builds already reviewed."""
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
            if build.submission_status is not Status.PENDING:
                return build
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

    async def _persist_prepared(self, build: Build, *, context: BuildEditContext, actor: Actor | None = None) -> None:
        """Save prepared values within a short write scope.

        For existing builds, the application lease covers this scope, and
        get_for_update holds a database row lock until commit. Embedding index
        updates run after both locks have been released.
        """
        # ORM identity columns may still be None before flush.
        if build.id is None:  # pyright: ignore[reportUnnecessaryComparison]
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
                    await self.authorize_edit(actor, current, uow=uow)
                await uow.builds.save(build)
                await uow.commit()
        await self._embeddings.index(build)

    async def _apply_patch(self, build: Build, patch: BuildEditPatch) -> None:
        """Apply a patch, including the showcase tags it cannot resolve itself, and prepare derived values."""
        patch.apply(build)
        if patch.showcase_tags is not UNSET:
            await replace_showcase_tags(build, patch.showcase_tags, self._taxonomy)
        await self._prepare_for_persistence(build)

    async def _prepare_for_persistence(self, build: Build) -> None:
        """Resolve application-owned defaults and derived values before a relational write."""
        # Resolve the editable taxonomy strings into tag assignments here, at the
        # last application-owned step, so the repository persists build.tags
        # verbatim and unresolvable names are recorded before anything is saved.
        await apply_build_taxonomy(build, self._taxonomy)
        await self._embeddings.prepare(build)

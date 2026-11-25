"""Create and finalize revision-guarded drafts against an approved build."""

from typing import Protocol
from uuid import UUID

from pydantic import JsonValue, TypeAdapter
from whenever import Instant

from squid.core.actors import Actor
from squid.core.builds.application.edits import (
    BUILD_EDIT,
    BuildFieldsEdit,
    BuildFieldsUnitOfWork,
    build_target,
    patch_values,
)
from squid.core.builds.application.services import BuildService
from squid.core.builds.domain import BuildStatus
from squid.core.builds.errors import BuildNotFoundError
from squid.core.builds.models import Build
from squid.core.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.core.errors import AuthorizationError, ValidationError
from squid.core.permissions.application import PermissionService
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_CREATE
from squid.core.submissions.application.build_answers import BuildAnswers, answers_from_build, patch_from_answers
from squid.core.submissions.application.drafts import (
    DraftRepository,
    DraftUnitOfWork,
    StoredDraft,
    SubmissionDraftService,
)
from squid.core.submissions.application.forms import SubmissionFormService
from squid.core.submissions.domain import DraftStatus, SubmissionOrigin
from squid.core.submissions.domain.build_edit import DraftEditResult, DraftEditSource
from squid.core.submissions.errors import DraftStateConflictError
from squid.core.unit_of_work import UnitOfWorkFactory


class EditDraftRepository(DraftRepository, Protocol):
    async def lock_edit(self, draft_id: UUID) -> StoredDraft: ...

    async def finish_edit(self, draft_id: UUID, result: DraftEditResult, now: Instant) -> None: ...


class BuildDraftUnitOfWork(BuildFieldsUnitOfWork, DraftUnitOfWork, Protocol):
    @property
    def submission_drafts(self) -> EditDraftRepository: ...


class BuildDraftEditor:
    def __init__(
        self,
        drafts: SubmissionDraftService,
        builds: BuildService,
        forms: SubmissionFormService,
        permissions: PermissionService,
        factory: UnitOfWorkFactory[BuildDraftUnitOfWork],
    ) -> None:
        self.drafts = drafts
        self.builds = builds
        self.forms = forms
        self.permissions = permissions
        self.factory = factory
        self.handler = BuildFieldsEdit(builds, suggestions=True)

    @staticmethod
    def public_answers(build: Build) -> BuildAnswers:
        saved = answers_from_build(build)
        # Credits retain provider subjects; server directions may also be private.
        for key in ("credits", "server_ip", "coordinates", "command_to_get_to_build"):
            saved.answers.pop(key, None)
        return saved

    async def create(
        self, build_id: int, category: str, actor: Actor, capabilities: frozenset[str], locale: str | None
    ) -> StoredDraft:
        await self.permissions.require(actor, BUILD_SUBMISSION_CREATE)
        build = await self.builds.get(build_id)
        if build is None or build.submission_status is not BuildStatus.APPROVED:
            raise BuildNotFoundError(build_id)
        if build.category.value.lower() != category:
            message = "Draft category must match the source build."
            raise ValidationError(message)
        assert actor.account_id is not None
        answers = TypeAdapter(dict[str, JsonValue]).validate_python(self.public_answers(build).answers)
        return await self.drafts.create(
            owner_account_id=actor.account_id,
            category=category,
            origin=SubmissionOrigin.WEB,
            client_capabilities=capabilities,
            locale=locale,
            edit_source=DraftEditSource(build_id=build_id, revision=build.revision, answers=answers),
        )

    async def finish(self, draft_id: UUID, actor: Actor, locale: str | None) -> DraftEditResult:
        async with self.factory() as uow:
            current = await uow.submission_drafts.lock_edit(draft_id)
            await self.drafts.get_accessible_in(uow, draft_id, actor)
            await self.permissions.require(actor, BUILD_SUBMISSION_CREATE, uow=uow)
            source = current.edit_source
            assert source is not None
            if source.result is not None:
                return source.result
            if current.snapshot.status not in {DraftStatus.EDITING, DraftStatus.NEEDS_ATTENTION}:
                raise DraftStateConflictError(current.snapshot.status.value, operation="submit")
            build = await uow.builds.get_for_update(source.build_id)
            if build is None or build.submission_status is not BuildStatus.APPROVED:
                raise BuildNotFoundError(source.build_id)
            expected = Revision(build_target(build.id), source.revision)
            saved = self.public_answers(build)
            if build.revision != source.revision:
                changes = [
                    {"field": key, "before": source.answers.get(key), "after": saved.answers.get(key)}
                    for key in sorted(source.answers.keys() | saved.answers.keys())
                    if source.answers.get(key) != saved.answers.get(key)
                ]
                raise RevisionConflictError(expected, build.revision).with_context(
                    public_context={"expected": source.revision, "current": build.revision, "changes": changes}
                )
            answers = current.snapshot.answers
            if answers.keys() - source.answers.keys():
                raise AuthorizationError
            if answers == source.answers:
                result = DraftEditResult(build_id=build.id, revision=build.revision, review_vote_id=None)
                await uow.submission_drafts.finish_edit(draft_id, result, Instant.now())
                await uow.commit()
                return result
            options = {
                key: (await self.forms.options(key, current.snapshot.category, locale=locale)).options
                for field, key in (("restrictions", "approved_restrictions"), ("patterns", "approved_patterns"))
                if answers.get(field) != saved.answers.get(field)
            }
            for field, catalogue in (("restrictions", "approved_restrictions"), ("patterns", "approved_patterns")):
                keys = answers.get(field)
                if (
                    keys != source.answers.get(field)
                    and isinstance(keys, list)
                    and any(key not in {choice.value for choice in options[catalogue]} for key in keys)
                ):
                    message = "The selected catalogue option is no longer available."
                    raise ValidationError(message)
            patch = patch_from_answers(saved, answers, saved.unresolved, options)
            command = EditCommand(BUILD_EDIT, (expected,), patch_values(patch), draft_id)
            prepared = await self.handler.prepare(uow, actor, command)
            revisions = (
                await self.handler.apply(uow, actor, command, prepared) if prepared.changed else prepared.revisions
            )
            result = DraftEditResult(
                build_id=build.id, revision=revisions[0].value, review_vote_id=prepared.review_vote_id
            )
            await uow.submission_drafts.finish_edit(draft_id, result, Instant.now())
            await uow.commit()
            return result

"""Versioned build edits with the existing build audit scope.

Conditional edits resolve tags before locking the build row.
Unconditional edits hold the lock throughout preparation to use the latest state.
The final revision and permission check, audit entry, and recoverable operation
outcome share one transaction. The row lock provides exclusion with every build
writer, including callers which also use the longer application lease.
"""

from dataclasses import dataclass
from typing import Protocol, cast

from squid.core.actors import ActingContext, Actor
from squid.core.builds.application.edit_schema import BuildPatch
from squid.core.builds.application.edit_values import build_values as build_values
from squid.core.builds.application.edit_values import changed_build_values
from squid.core.builds.application.edit_values import patch_values as patch_values
from squid.core.builds.application.editing import BuildEditPatch
from squid.core.builds.application.ports import BuildUnitOfWork
from squid.core.builds.application.services import BuildService
from squid.core.builds.domain import BuildStatus, Info, UnresolvedTags
from squid.core.builds.domain.edit_policy import BuildEditDecision
from squid.core.builds.domain.tags import RESTRICTION_FIELDS
from squid.core.builds.errors import BuildNotFoundError
from squid.core.builds.models import Build
from squid.core.edits.application import EditUnitOfWork
from squid.core.edits.domain import (
    EditCommand,
    EditTarget,
    EditValues,
    LatestRevision,
    PreparedEdit,
    Revision,
    RevisionConflictError,
    require_revision,
)
from squid.core.errors import (
    AccountRequiredError,
    AuthorizationError,
    DataIntegrityError,
    InvalidStateError,
    ValidationError,
)
from squid.core.permissions.application.ports import PermissionUnitOfWork
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_APPROVE, BUILD_SUBMISSION_REJECT
from squid.core.settings.application.ports import SettingsUnitOfWork
from squid.core.settings.domain.runtime import RuntimeSettings
from squid.core.voting.domain import BuildEditVoteTarget

BUILD_STATUS_EDIT = "build.status"

BUILD_EDIT = "build.fields"


def build_target(build_id: int) -> EditTarget:
    return EditTarget("build", str(build_id))


class BuildEditUnitOfWork(BuildUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    pass


class BuildEditVoteWriter(Protocol):
    async def create_build_edit_session(
        self, *, author_account_id: int, pass_threshold: int, fail_threshold: int, target: BuildEditVoteTarget
    ) -> int: ...


class BuildFieldsUnitOfWork(BuildEditUnitOfWork, SettingsUnitOfWork, Protocol):
    @property
    def votes(self) -> BuildEditVoteWriter: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedBuildEdit(PreparedEdit):
    build: Build


class BuildFieldsEdit:
    def __init__(self, builds: BuildService) -> None:
        self._builds = builds

    async def prepare(self, uow: BuildFieldsUnitOfWork, actor: Actor, command: EditCommand) -> PreparedBuildEdit:
        if actor.linked_account_id is None:
            raise AccountRequiredError
        if len(command.expected) != 1 or command.expected[0].target.kind != "build":
            raise ValidationError
        expected = command.expected[0]
        build_id = int(expected.target.key)
        values = command.values.unpack()
        unresolved = values.pop("unresolved_tags", values.pop("unresolved_taxonomy", None))
        patch = BuildPatch.from_patch(BuildEditPatch.from_attributes(values))
        if isinstance(expected, LatestRevision):
            # Latest-state edits must serialize preparation too, since their patch
            # and receipt are based on whichever version holds the lock first.
            current = await uow.builds.get_for_update(build_id)
            if current is None:
                raise BuildNotFoundError(build_id)
            expected = require_revision(expected, current.revision)
        build = await self._builds.get(build_id, context=ActingContext.current())
        if build is None:
            raise BuildNotFoundError(build_id)
        if build.revision != expected.value:
            raise RevisionConflictError(expected, build.revision)
        before = build_values(build)
        attributes = patch.edit_attributes()
        if unresolved is not None:
            if not isinstance(unresolved, dict) or unresolved.keys() - {"unknown_restrictions", "unknown_patterns"}:
                raise ValidationError
            try:
                retained = UnresolvedTags.read(cast(Info, unresolved))
            except DataIntegrityError as error:
                raise ValidationError from error
            # Explicit tag patches replace their retained unknown names. Feed
            # inverse proposals through the same resolver alongside known names.
            for name in RESTRICTION_FIELDS:
                attributes[name] = [*cast(list[str], attributes.get(name, [])), *retained.restriction_names(name)]
            attributes["door_type"] = [*cast(list[str], attributes.get("door_type", [])), *retained.patterns]
        candidate = BuildEditPatch.from_attributes(attributes)
        await self._builds.prepare_edit(build, candidate, context=ActingContext.for_actor(actor))
        current = await uow.builds.get_for_update(build_id)
        if current is None:
            raise BuildNotFoundError(build_id)
        require_revision(expected, current.revision)
        after = build_values(build)
        changes = changed_build_values(before, after)
        changed = {key for key, _, _ in changes}
        decision = await self._builds.edit_decision(current, changed, uow=uow, context=ActingContext.for_actor(actor))
        if decision is BuildEditDecision.DENY or (decision is BuildEditDecision.REVIEW and command.purpose != "edit"):
            raise AuthorizationError(public_context={"fields": sorted(changed)})
        if decision is BuildEditDecision.REVIEW:
            settings = await uow.settings.runtime(RuntimeSettings())
            vote_id = await uow.votes.create_build_edit_session(
                author_account_id=actor.linked_account_id,
                pass_threshold=settings.build_vote_pass_threshold,
                fail_threshold=settings.build_vote_fail_threshold,
                target=BuildEditVoteTarget(build_id, expected.value, patch_values(candidate).unpack(), changes),
            )
            return PreparedBuildEdit(
                before, before, final=True, review_vote_id=vote_id, build=build, revisions=(expected,)
            )
        return PreparedBuildEdit(before, after, build=build, revisions=(expected,))

    async def apply(
        self, uow: BuildEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedBuildEdit)
        assert actor.linked_account_id is not None
        async with uow.builds.editing(
            actor=actor.linked_account_id,
            source="versioned_editor",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            await uow.builds.save(prepared.build)
        return (Revision(command.expected[0].target, prepared.build.revision),)


class BuildStatusEdit:
    """Review a pending build, or reverse a review at its recorded revision."""

    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    async def prepare(self, uow: BuildFieldsUnitOfWork, actor: Actor, command: EditCommand) -> PreparedBuildEdit:
        if actor.linked_account_id is None:
            raise AccountRequiredError
        if len(command.expected) != 1 or command.expected[0].target.kind != "build":
            raise ValidationError
        values = command.values.unpack()
        if values.keys() != {"submission_status"}:
            raise ValidationError
        try:
            status = BuildStatus(values["submission_status"])
        except (ValueError, TypeError) as error:
            raise ValidationError from error
        build_id = int(command.expected[0].target.key)
        build = await uow.builds.get_for_update(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        expected = require_revision(command.expected[0], build.revision)
        previous = build.submission_status
        if command.purpose == "undo":
            if status is not BuildStatus.PENDING or previous not in (BuildStatus.APPROVED, BuildStatus.REJECTED):
                raise InvalidStateError
            decision = previous
        else:
            if previous is not BuildStatus.PENDING or status not in (BuildStatus.APPROVED, BuildStatus.REJECTED):
                raise InvalidStateError
            decision = status
        permission = BUILD_SUBMISSION_APPROVE if decision is BuildStatus.APPROVED else BUILD_SUBMISSION_REJECT
        await self._permissions.require(actor, permission, uow=uow)
        assert previous is not None
        return PreparedBuildEdit(
            EditValues.of({"submission_status": previous.value}),
            EditValues.of({"submission_status": status.value}),
            revisions=(expected,),
            build=build,
        )

    async def apply(
        self, uow: BuildEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedBuildEdit)
        assert actor.linked_account_id is not None
        async with uow.builds.editing(
            actor=actor.linked_account_id,
            source="versioned_review",
            operation="moderation",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            await uow.builds.set_status(prepared.build, BuildStatus(prepared.after.unpack()["submission_status"]))
        return (Revision(command.expected[0].target, prepared.build.revision),)

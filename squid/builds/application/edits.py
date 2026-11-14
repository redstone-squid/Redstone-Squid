"""Versioned build edits with the existing build audit scope.

Conditional edits prepare taxonomy and embeddings before locking the build row.
Unconditional edits hold the lock throughout preparation to use the latest state.
The final revision and permission check, audit entry, and recoverable operation
outcome share one transaction. The row lock provides exclusion with every build
writer, including callers which also use the longer application lease.
"""

import json
from dataclasses import dataclass
from typing import Protocol, cast

from squid.builds.application.edit_schema import BuildPatch
from squid.builds.application.editing import BuildEditPatch
from squid.builds.application.ports import BuildUnitOfWork
from squid.builds.application.services import BuildEditor, BuildService
from squid.builds.domain import Info, Status, UnresolvedTaxonomy
from squid.builds.domain.taxonomy import RESTRICTION_FIELDS
from squid.builds.errors import BuildNotFoundError
from squid.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.actors import Actor
from squid.core.errors import (
    AccountRequiredError,
    DataIntegrityError,
    InvalidStateError,
    ValidationError,
)
from squid.core.sentinel import UNSET
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import (
    EditCommand,
    EditTarget,
    EditValues,
    LatestRevision,
    PreparedEdit,
    Revision,
    RevisionConflictError,
    require_revision,
)
from squid.permissions.application.ports import PermissionUnitOfWork
from squid.permissions.application.services import PermissionService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_APPROVE, BUILD_SUBMISSION_REJECT
from squid.tags.domain import TagSemanticKind

BUILD_STATUS_EDIT = "build.status"

BUILD_EDIT = "build.fields"


def build_target(build_id: int) -> EditTarget:
    return EditTarget("build", str(build_id))


def _patch_document(patch: BuildEditPatch) -> dict[str, object]:
    values = patch.attributes()
    if patch.credits is not UNSET:
        values["credits"] = [credit.to_json() for credit in patch.credits]
    return values


def patch_values(patch: BuildEditPatch) -> EditValues:
    return EditValues(json.dumps(_patch_document(patch)))


def build_values(build: Build) -> EditValues:
    """Capture editable facts without derived values or submission provenance."""
    server = build.extra_info.get("server_info", {})
    patch = BuildEditPatch(
        display_name=build.display_name,
        version_spec=build.version_spec,
        versions=list(build.versions),
        showcase_tags=[
            assignment.definition.stable_key
            for assignment in build.tags
            if assignment.definition.semantic_kind is TagSemanticKind.SHOWCASE
        ],
        dimensions=build.dimensions,
        door_type=list(build.patterns),
        wiring_placement_restrictions=list(build.wiring_placement_restrictions),
        animated_restrictions=list(build.animated_restrictions),
        component_restrictions=list(build.component_restrictions),
        miscellaneous_restrictions=list(build.miscellaneous_restrictions),
        extra_user_info=build.description,
        credits=list(build.credits),
        image_urls=list(build.image_urls),
        video_urls=list(build.video_urls),
        world_download_urls=list(build.world_download_urls),
        schematic_urls=list(build.schematic_urls),
        render_urls=list(build.render_urls),
        server_ip=server.get("server_ip"),
        coordinates=server.get("coordinates"),
        command_to_get_to_build=server.get("command_to_build"),
        completion_time=build.completion_time,
    )
    if isinstance(build, DoorBuild):
        patch = BuildEditPatch.combine(
            (
                patch,
                BuildEditPatch(
                    door_dimensions=build.door_dimensions,
                    door_orientation_type=build.orientation,
                    visible_opening_time=build.visible_opening_time,
                    visible_closing_time=build.visible_closing_time,
                    normal_opening_time=build.normal_opening_time,
                    normal_closing_time=build.normal_closing_time,
                ),
            )
        )
    if isinstance(build, ExtenderBuild):
        patch = BuildEditPatch.combine(
            (
                patch,
                BuildEditPatch(
                    extender_orientation=build.orientation,
                    extension_length=build.extension_length,
                    extension_time=build.extension_time,
                    retraction_time=build.retraction_time,
                ),
            )
        )
    unresolved: Info = {}
    UnresolvedTaxonomy.read(build.extra_info).write(unresolved)
    return EditValues(json.dumps(_patch_document(patch) | {"unresolved_taxonomy": unresolved}))


class BuildEditUnitOfWork(BuildUnitOfWork, EditUnitOfWork, PermissionUnitOfWork, Protocol):
    pass


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedBuildEdit(PreparedEdit):
    build: Build


class BuildFieldsEdit:
    def __init__(self, builds: BuildService) -> None:
        self._builds = builds

    async def prepare(self, uow: BuildEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedBuildEdit:
        if actor.account_id is None:
            raise AccountRequiredError
        if len(command.expected) != 1 or command.expected[0].target.kind != "build":
            raise ValidationError
        expected = command.expected[0]
        build_id = int(expected.target.key)
        values = command.values.unpack()
        unresolved = values.pop("unresolved_taxonomy", None)
        patch = BuildPatch.from_patch(BuildEditPatch.from_attributes(values))
        if isinstance(expected, LatestRevision):
            # Latest-state edits must serialize preparation too, since their patch
            # and receipt are based on whichever version holds the lock first.
            current = await uow.builds.get_for_update(build_id)
            if current is None:
                raise BuildNotFoundError(build_id)
            expected = require_revision(expected, current.revision)
        build = await self._builds.get(build_id)
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
                retained = UnresolvedTaxonomy.read(cast(Info, unresolved))
            except DataIntegrityError as error:
                raise ValidationError from error
            # Explicit taxonomy patches replace their retained unknown names. Feed
            # inverse proposals through the same resolver alongside known names.
            for name in RESTRICTION_FIELDS:
                attributes[name] = [*cast(list[str], attributes.get(name, [])), *retained.restriction_names(name)]
            attributes["door_type"] = [*cast(list[str], attributes.get("door_type", [])), *retained.patterns]
        editor = BuildEditor(actor, source="versioned_editor")
        await self._builds.prepare_edit(editor, build, BuildEditPatch.from_attributes(attributes))
        current = await uow.builds.get_for_update(build_id)
        if current is None:
            raise BuildNotFoundError(build_id)
        require_revision(expected, current.revision)
        await self._builds.authorize_edit_in(uow, editor, current)
        return PreparedBuildEdit(before, build_values(build), build=build, revisions=(expected,))

    async def apply(
        self, uow: BuildEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedBuildEdit)
        assert actor.account_id is not None
        async with uow.builds.editing(
            actor=actor.account_id,
            source="versioned_editor",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            # save also persists the prepared vector in PostgreSQL; no separate
            # post-commit index write can overtake a newer build revision.
            await uow.builds.save(prepared.build)
        return (Revision(command.expected[0].target, prepared.build.revision),)


class BuildStatusEdit:
    """Review a pending build, or reverse a review at its recorded revision."""

    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    async def prepare(self, uow: BuildEditUnitOfWork, actor: Actor, command: EditCommand) -> PreparedBuildEdit:
        if actor.account_id is None:
            raise AccountRequiredError
        if len(command.expected) != 1 or command.expected[0].target.kind != "build":
            raise ValidationError
        values = command.values.unpack()
        if values.keys() != {"submission_status"}:
            raise ValidationError
        try:
            status = Status(values["submission_status"])
        except (ValueError, TypeError) as error:
            raise ValidationError from error
        build_id = int(command.expected[0].target.key)
        build = await uow.builds.get_for_update(build_id)
        if build is None:
            raise BuildNotFoundError(build_id)
        expected = require_revision(command.expected[0], build.revision)
        previous = build.submission_status
        if command.purpose == "undo":
            if status is not Status.PENDING or previous not in (Status.CONFIRMED, Status.DENIED):
                raise InvalidStateError
            decision = previous
        else:
            if previous is not Status.PENDING or status not in (Status.CONFIRMED, Status.DENIED):
                raise InvalidStateError
            decision = status
        permission = BUILD_SUBMISSION_APPROVE if decision is Status.CONFIRMED else BUILD_SUBMISSION_REJECT
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
        assert actor.account_id is not None
        async with uow.builds.editing(
            actor=actor.account_id,
            source="versioned_review",
            operation="moderation",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            await uow.builds.set_status(prepared.build, Status(prepared.after.unpack()["submission_status"]))
        return (Revision(command.expected[0].target, prepared.build.revision),)

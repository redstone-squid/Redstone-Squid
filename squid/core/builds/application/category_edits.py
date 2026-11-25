"""Revision-checked moderation changes to a build's category."""

from dataclasses import dataclass
from functools import cache
from typing import Protocol, Self, override

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic_core import PydanticCustomError

from squid.core.actors import Actor
from squid.core.builds.application.edit_schema import DoorPatch, ExtenderPatch
from squid.core.builds.application.edit_values import build_values
from squid.core.builds.application.editing import BuildEditPatch
from squid.core.builds.application.edits import build_target
from squid.core.builds.application.ports import BuildRepository, BuildUnitOfWork
from squid.core.builds.domain import BuildCategory
from squid.core.builds.errors import BuildEditValidationError, BuildNotFoundError
from squid.core.builds.models import BUILD_CLASS_BY_CATEGORY, Build
from squid.core.edits.application import EditUnitOfWork
from squid.core.edits.domain import EditCommand, EditValues, PreparedEdit, Revision, require_revision
from squid.core.errors import ValidationError
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_RECLASSIFY
from squid.core.submissions.application.build_answers import answers_from_build
from squid.core.submissions.application.forms import build_edit_manifest
from squid.core.submissions.domain import SubmissionOrigin

_INVALID_VALUE = "value_error"
BUILD_CATEGORY_EDIT = "build.category"
_edit_manifest = cache(build_edit_manifest)


class BuildCategoryChange(BaseModel):
    """The destination and any known facts particular to that category."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: BuildCategory
    door: DoorPatch | None = None
    extender: ExtenderPatch | None = None

    @model_validator(mode="after")
    def validate_details(self) -> Self:
        if self.door is not None and (
            self.category is not BuildCategory.DOOR or "patterns" in self.door.model_fields_set
        ):
            msg = "Door details must match the selected category. Patterns are edited on the saved build."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        if self.extender is not None and self.category is not BuildCategory.EXTENDER:
            msg = "Extender details must match the selected category."
            raise PydanticCustomError(_INVALID_VALUE, msg)
        return self

    def target(self, build_id: int) -> Build:
        """Build a validated subtype from the supplied facts. Unknown facts stay empty."""
        target = BUILD_CLASS_BY_CATEGORY[self.category](id=build_id)
        details = self.door if self.door is not None else self.extender
        if details is not None:
            BuildEditPatch.from_attributes(details.edit_attributes()).apply(target)
        problems = _edit_manifest().answer_issues(
            self.category.value.lower(), answers_from_build(target).answers, origin=SubmissionOrigin.DISCORD
        )
        if problems:
            raise BuildEditValidationError(problems)
        return target


class BuildCategoryRepository(BuildRepository, Protocol):
    async def change_category(self, current: Build, target: Build) -> int: ...


class BuildCategoryUnitOfWork(BuildUnitOfWork, EditUnitOfWork, Protocol):
    @property
    @override
    def builds(self) -> BuildCategoryRepository: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedCategoryChange(PreparedEdit):
    current: Build
    target: Build


class BuildCategoryEdit:
    """Replace just the subtype while preserving the aggregate's identity and shared facts."""

    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

    async def prepare(self, uow: BuildCategoryUnitOfWork, actor: Actor, command: EditCommand) -> PreparedCategoryChange:
        if command.purpose != "edit" or len(command.expected) != 1 or command.expected[0].target.kind != "build":
            raise ValidationError
        actor.require_account_id()
        await self._permissions.require(actor, BUILD_SUBMISSION_RECLASSIFY, uow=uow)
        expected = command.expected[0]
        build_id = int(expected.target.key)
        current = await uow.builds.get_for_update(build_id)
        if current is None:
            raise BuildNotFoundError(build_id)
        revision = require_revision(expected, current.revision)
        change = BuildCategoryChange.model_validate(command.values.unpack())
        if current.category is change.category:
            msg = "Choose a different category."
            raise ValidationError(msg)
        target = change.target(build_id)
        # The shared facts are untouched. A category change is recorded in database
        # history but cannot be replayed by the generic editor's undo button.
        return PreparedCategoryChange(
            EditValues.of({"category": current.category.value}),
            EditValues.of({"category": target.category.value}).patch(build_values(target)),
            final=True,
            revisions=(revision,),
            current=current,
            target=target,
        )

    async def apply(
        self, uow: BuildCategoryUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        assert isinstance(prepared, PreparedCategoryChange)
        async with uow.builds.editing(
            actor=actor.require_account_id(),
            source="build_category_change",
            operation="moderation",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            revision = await uow.builds.change_category(prepared.current, prepared.target)
        return (Revision(build_target(prepared.current.id), revision),)

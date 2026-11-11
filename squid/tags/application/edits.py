"""Showcase assignments participate in the owning build's revision and audit."""

from dataclasses import dataclass, replace
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from whenever import Instant

from squid.accounts.application.edits import AccountEditUnitOfWork
from squid.accounts.errors import ConsentRequiredError
from squid.builds.application.ports import BuildUnitOfWork
from squid.builds.errors import BuildNotFoundError
from squid.builds.models import Build
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ValidationError
from squid.edits.application import EditUnitOfWork
from squid.edits.domain import EditCommand, EditValues, PreparedEdit, Revision
from squid.edits.fields import Aggregate, FieldCommand
from squid.tags.application.history import AssignmentSnapshot
from squid.tags.application.services import TagDefinitionRepository, coerce_assignment_value, require_showcase
from squid.tags.domain import TagValue
from squid.tags.models import TagDefinition

SHOWCASE_EDIT = "tags.assignment"


class ShowcaseValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tag_id: int = Field(gt=0)
    assigned: bool = True
    value: str | None = None

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())


class TagEditStore(TagDefinitionRepository, Protocol):
    async def get_for_update(self, tag_id: int) -> TagDefinition | None: ...

    async def advance_build_revision(self, build_id: int, expected: Revision) -> Revision: ...


class TagEditUnitOfWork(BuildUnitOfWork, AccountEditUnitOfWork, EditUnitOfWork, Protocol):
    @property
    def tags(self) -> TagEditStore: ...


@dataclass(slots=True)
class AssignmentRoot:
    build: Build
    definition: TagDefinition
    assignment: AssignmentSnapshot | None
    actor_id: int | None = None


def assignment_value(value: TagValue) -> str | None:
    if value is None:
        return None
    return str(value).lower() if isinstance(value, bool) else str(value)


class ShowcaseAssignmentEdit(FieldCommand[TagEditUnitOfWork, AssignmentRoot, ShowcaseValues]):
    def __init__(self) -> None:
        async def load(uow: TagEditUnitOfWork, command: EditCommand) -> AssignmentRoot:
            values = ShowcaseValues.model_validate_json(command.values.encoded)
            build_id = int(command.expected[0].target.key)
            build = await uow.builds.get_for_update(build_id)
            if build is None:
                raise BuildNotFoundError(build_id)
            definition = require_showcase(await uow.tags.get_for_update(values.tag_id))
            return AssignmentRoot(build, definition, await uow.tags.assignment(build_id, values.tag_id))

        async def authorize(uow: TagEditUnitOfWork, actor: Actor, root: AssignmentRoot) -> None:
            account_id = actor.require_account_id()
            if root.build.submitter_account_id != account_id:
                raise AuthorizationError
            if not await uow.accounts.has_current_consent(account_id):
                raise ConsentRequiredError(account_id=account_id)
            root.actor_id = account_id

        async def flush(uow: TagEditUnitOfWork, root: AssignmentRoot, expected: Revision) -> Revision:
            assert root.build.id is not None
            return await uow.tags.advance_build_revision(root.build.id, expected)

        super().__init__(Aggregate("build", load, lambda root: root.build.revision, flush), ShowcaseValues, authorize)

    async def capture(self, uow: TagEditUnitOfWork, root: AssignmentRoot, command: EditCommand) -> ShowcaseValues:
        return ShowcaseValues(
            tag_id=root.definition.id,
            assigned=root.assignment is not None,
            value=None if root.assignment is None else assignment_value(root.assignment.value),
        )

    async def validate(
        self,
        uow: TagEditUnitOfWork,
        actor: Actor,
        root: AssignmentRoot,
        command: EditCommand,
        before: ShowcaseValues,
        after: ShowcaseValues,
    ) -> ShowcaseValues:
        value = coerce_assignment_value(root.definition, after.value) if after.assigned else None
        return ShowcaseValues(tag_id=after.tag_id, assigned=after.assigned, value=assignment_value(value))

    async def write(
        self, uow: TagEditUnitOfWork, root: AssignmentRoot, before: ShowcaseValues, after: ShowcaseValues
    ) -> None:
        assert root.build.id is not None and root.actor_id is not None
        desired = None
        if after.assigned:
            value = coerce_assignment_value(root.definition, after.value)
            desired = (
                replace(root.assignment, value=value)
                if root.assignment is not None
                else AssignmentSnapshot(
                    root.definition.value_type, value, None, None, None, "submitted", root.actor_id, Instant.now()
                )
            )
        if not await uow.tags.replace_assignment(
            root.build.id, after.tag_id, root.assignment, desired, actor_account_id=root.actor_id
        ):
            raise ValidationError

    async def apply(
        self, uow: TagEditUnitOfWork, actor: Actor, command: EditCommand, prepared: PreparedEdit
    ) -> tuple[Revision, ...]:
        async with uow.builds.editing(
            actor=actor.require_account_id(),
            source="showcase_assignment",
            correlation_kind="edit_operation",
            correlation_id=str(command.operation_id),
        ):
            return await super().apply(uow, actor, command, prepared)

"""Versioned edits to the tag library: proposals, moderation, and restriction aliases."""

from dataclasses import dataclass, replace
from typing import Annotated, Protocol, Self, override
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from squid.core.accounts.application.edits import AccountEditUnitOfWork
from squid.core.accounts.errors import ConsentRequiredError
from squid.core.actors import Actor
from squid.core.builds.application.restrictions import RestrictionUnitOfWork
from squid.core.edits.application import EditUnitOfWork
from squid.core.edits.domain import EditCommand, EditTarget, EditValues, Revision
from squid.core.edits.fields import Aggregate, FieldCommand, PreparedFields
from squid.core.errors import ConflictError, NotFoundError, OwnershipRequiredError, ValidationError
from squid.core.permissions.application.services import PermissionService
from squid.core.permissions.domain.catalogue import (
    RESTRICTION_ALIAS_CREATE,
    TAG_PROPOSAL_APPROVE,
    TAG_PROPOSAL_ARCHIVE,
    TAG_PROPOSAL_REJECT,
)
from squid.core.tags.application.services import (
    TagDefinitionRepository,
    normalize_proposal,
)
from squid.core.tags.domain import (
    TagAuthority,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.core.tags.models import TagDefinition


class TagEditRepository(TagDefinitionRepository, Protocol):
    async def proposal_for_update(self, tag_id: int) -> TagDefinition | None: ...

    async def create_reserved(self, definition: TagDefinition) -> None: ...

    async def is_unused(self, tag_id: int) -> bool: ...

    async def query_name_available(self, name: str, tag_id: int) -> bool: ...

    async def get_for_update(self, tag_id: int) -> TagDefinition | None: ...

    async def flush_definition_revision(self, definition: TagDefinition, expected: Revision) -> Revision: ...


class TagEditUnitOfWork(AccountEditUnitOfWork, EditUnitOfWork, Protocol):
    @property
    def tags(self) -> TagEditRepository: ...


ALIAS_EDIT = "tags.alias"


def tag_target(tag_id: int) -> EditTarget:
    return EditTarget("tag", str(tag_id))


class AliasValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    present: bool = True

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())


class TagAliasUnitOfWork(TagEditUnitOfWork, RestrictionUnitOfWork, Protocol):
    pass


class TagAliasEdit(FieldCommand[TagAliasUnitOfWork, TagDefinition, AliasValues]):
    def __init__(self, permissions: PermissionService) -> None:
        async def load(uow: TagAliasUnitOfWork, command: EditCommand) -> TagDefinition:
            values = AliasValues.model_validate_json(command.values.encoded)
            # Existing alias writers acquire the name lock before the definition lock.
            await uow.restrictions.lock_alias(values.alias)
            definition = await uow.tags.get_for_update(int(command.expected[0].target.key))
            if (
                definition is None
                or definition.authority is not TagAuthority.OFFICIAL
                or definition.semantic_kind is not TagSemanticKind.RESTRICTION
            ):
                raise ValidationError
            return definition

        async def authorize(uow: TagAliasUnitOfWork, actor: Actor, root: TagDefinition) -> None:
            await permissions.require(actor, RESTRICTION_ALIAS_CREATE, uow=uow)

        async def flush(uow: TagAliasUnitOfWork, root: TagDefinition, expected: Revision) -> Revision:
            return await uow.tags.flush_definition_revision(root, expected)

        super().__init__(Aggregate("tag", load, lambda root: root.revision, flush), AliasValues, authorize)

    @override
    async def capture(self, uow: TagAliasUnitOfWork, root: TagDefinition, command: EditCommand) -> AliasValues:
        values = AliasValues.model_validate_json(command.values.encoded)
        return AliasValues(alias=values.alias, present=await uow.restrictions.has_alias(root.id, values.alias))

    @override
    async def write(
        self, uow: TagAliasUnitOfWork, root: TagDefinition, before: AliasValues, after: AliasValues
    ) -> None:
        if not await uow.restrictions.replace_alias(root.display_name, after.alias, before.present, after.present):
            raise ValidationError


PROPOSAL_EDIT = "tags.proposal"
MODERATION_EDIT = "tags.moderation"


class ProposalValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str
    value_type: TagValueType
    query_name: str | None = None
    stable_key: str = Field(default_factory=lambda: f"user_{uuid4().hex}", pattern=r"^user_[0-9a-f]{32}$")
    present: bool = True

    @model_validator(mode="after")
    def normalize(self) -> Self:
        self.display_name, self.query_name = normalize_proposal(self.display_name, self.query_name)
        return self

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())

    @classmethod
    def capture(cls, row: TagDefinition) -> ProposalValues:
        return cls(
            display_name=row.display_name,
            value_type=row.value_type,
            query_name=row.query_name,
            stable_key=row.stable_key,
            present=not row.withdrawn,
        )


@dataclass(slots=True)
class ProposalRoot:
    identity: int
    definition: TagDefinition | None
    actor_id: int | None = None


class TagProposalEdit(FieldCommand[TagEditUnitOfWork, ProposalRoot, ProposalValues]):
    def __init__(self) -> None:
        async def load(uow: TagEditUnitOfWork, command: EditCommand) -> ProposalRoot:
            identity = int(command.expected[0].target.key)
            return ProposalRoot(identity, await uow.tags.proposal_for_update(identity))

        async def authorize(uow: TagEditUnitOfWork, actor: Actor, root: ProposalRoot) -> None:
            account_id = actor.require_account_id()
            if root.definition is not None and root.definition.created_by_account_id != account_id:
                raise OwnershipRequiredError
            if not await uow.accounts.has_current_consent(account_id):
                raise ConsentRequiredError(account_id=account_id)
            root.actor_id = account_id

        async def flush(uow: TagEditUnitOfWork, root: ProposalRoot, expected: Revision) -> Revision:
            assert root.definition is not None
            return await uow.tags.flush_definition_revision(root.definition, expected)

        super().__init__(
            Aggregate("tag", load, lambda root: 0 if root.definition is None else root.definition.revision, flush),
            ProposalValues,
            authorize,
        )

    @override
    async def capture(self, uow: TagEditUnitOfWork, root: ProposalRoot, command: EditCommand) -> ProposalValues:
        if root.definition is not None:
            return ProposalValues.capture(root.definition)
        values = ProposalValues.model_validate_json(command.values.encoded)
        return values.model_copy(update={"present": False})

    @override
    async def validate(
        self,
        uow: TagEditUnitOfWork,
        actor: Actor,
        root: ProposalRoot,
        command: EditCommand,
        before: ProposalValues,
        after: ProposalValues,
    ) -> ProposalValues:
        if root.definition is None:
            if not after.present:
                raise NotFoundError
        elif (
            root.definition.moderation_status is not TagModerationStatus.PENDING
            or root.definition.authority is not TagAuthority.USER
            or before.model_copy(update={"present": after.present}) != after
            or not await uow.tags.is_unused(root.identity)
        ):
            raise ConflictError
        if (
            after.present
            and after.query_name is not None
            and not await uow.tags.query_name_available(after.query_name, root.identity)
        ):
            raise ConflictError
        return after

    @override
    async def write(
        self, uow: TagEditUnitOfWork, root: ProposalRoot, before: ProposalValues, after: ProposalValues
    ) -> None:
        if root.definition is None:
            root.definition = TagDefinition(
                id=root.identity,
                stable_key=after.stable_key,
                display_name=after.display_name,
                normalized_name=after.display_name.casefold(),
                query_name=after.query_name,
                authority=TagAuthority.USER,
                semantic_kind=TagSemanticKind.SHOWCASE,
                value_type=after.value_type,
                moderation_status=TagModerationStatus.PENDING,
                created_by_account_id=root.actor_id,
                render_template="{name}" if after.value_type is TagValueType.NONE else "{name}: {value}{unit}",
            )
            await uow.tags.create_reserved(root.definition)
        else:
            root.definition.withdrawn = not after.present


class ModerationValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: TagModerationStatus

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())


class TagModerationEdit(FieldCommand[TagEditUnitOfWork, TagDefinition, ModerationValues]):
    def __init__(self, permissions: PermissionService) -> None:
        self._permissions = permissions

        async def load(uow: TagEditUnitOfWork, command: EditCommand) -> TagDefinition:
            definition = await uow.tags.get_for_update(int(command.expected[0].target.key))
            if definition is None or definition.withdrawn:
                raise NotFoundError
            return definition

        async def authorize(uow: TagEditUnitOfWork, actor: Actor, root: TagDefinition) -> None:
            pass  # The required permission depends on the requested moderation status.

        async def flush(uow: TagEditUnitOfWork, root: TagDefinition, expected: Revision) -> Revision:
            return await uow.tags.flush_definition_revision(root, expected)

        super().__init__(Aggregate("tag", load, lambda root: root.revision, flush), ModerationValues, authorize)

    @override
    async def capture(self, uow: TagEditUnitOfWork, root: TagDefinition, command: EditCommand) -> ModerationValues:
        return ModerationValues(status=root.moderation_status)

    @override
    async def validate(
        self,
        uow: TagEditUnitOfWork,
        actor: Actor,
        root: TagDefinition,
        command: EditCommand,
        before: ModerationValues,
        after: ModerationValues,
    ) -> ModerationValues:
        node = {
            TagModerationStatus.APPROVED: TAG_PROPOSAL_APPROVE,
            TagModerationStatus.REJECTED: TAG_PROPOSAL_REJECT,
            TagModerationStatus.ARCHIVED: TAG_PROPOSAL_ARCHIVE,
        }.get(after.status)
        if node is None:
            raise ValidationError
        await self._permissions.require(actor, node, uow=uow)
        return after

    @override
    async def prepare(
        self, uow: TagEditUnitOfWork, actor: Actor, command: EditCommand
    ) -> PreparedFields[TagDefinition, ModerationValues]:
        return replace(await super().prepare(uow, actor, command), final=True)

    @override
    async def write(
        self, uow: TagEditUnitOfWork, root: TagDefinition, before: ModerationValues, after: ModerationValues
    ) -> None:
        await uow.tags.set_status(root.id, after.status)

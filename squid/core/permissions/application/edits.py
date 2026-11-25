"""Permission commands share one graph revision and the administration policy."""

from dataclasses import dataclass, replace
from typing import Protocol, Self, override

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from whenever import Instant

from squid.core.actors import ActingContext, Actor, UserActor
from squid.core.edits.application import EditUnitOfWork
from squid.core.edits.domain import EditCommand, EditTarget, EditValues, Revision
from squid.core.edits.fields import Aggregate, FieldCommand, PreparedFields
from squid.core.errors import ConflictError, NotFoundError, ValidationError
from squid.core.permissions.application.administration import MAX_ROLE_RANK, PermissionAdministrationService
from squid.core.permissions.application.ports import AuditEntry, PermissionAdminRepository, PermissionAdminUnitOfWork
from squid.core.permissions.domain import Effect
from squid.core.permissions.domain.models import (
    AccountGrantSubject,
    AuditAction,
    ExternalRoleGrantSubject,
    GrantSubject,
    PatternMode,
)
from squid.core.permissions.models import PermissionRole

PERMISSION_GRAPH = EditTarget("permission-graph", "1")
GRANT_EDIT = "permissions.grant"
BULK_ASSIGNMENT_EDIT = "permissions.assignments"
ASSIGNMENT_EDIT = "permissions.assignment"
RANK_EDIT = "permissions.rank"
PATTERN_EDIT = "permissions.pattern"
INCLUDE_EDIT = "permissions.include"


class PermissionEditRepository(PermissionAdminRepository, Protocol):
    async def lock_graph(self) -> int: ...

    async def role_identity(self, role_id: int) -> PermissionRole | None: ...

    async def unused_role(self, role_id: int) -> bool: ...

    async def role_slug_available(self, slug: str, guild_id: int | None, role_id: int) -> bool: ...

    async def save_role_presence(self, role: PermissionRole, *, audit: AuditEntry) -> None: ...


class PermissionEditUnitOfWork(PermissionAdminUnitOfWork, EditUnitOfWork, Protocol):
    @property
    @override
    def permission_admin(self) -> PermissionEditRepository: ...


class PermissionValues(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def edit_values(self) -> EditValues:
        return EditValues(self.model_dump_json())


class SubjectValues(PermissionValues):
    account_id: int | None = None
    discord_role_id: int | None = None
    guild_id: int | None = None
    scope_guild_id: int | None = None
    expires_at: AwareDatetime | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def one_subject(self) -> Self:
        if (self.account_id is None) == (self.discord_role_id is None):
            raise ValidationError
        if self.discord_role_id is not None and self.guild_id is None:
            raise ValidationError
        return self

    @property
    def subject(self) -> GrantSubject:
        """Translate validated transport fields into one explicit application principal."""
        if self.account_id is not None:
            return AccountGrantSubject(self.account_id)
        assert self.discord_role_id is not None and self.guild_id is not None
        return ExternalRoleGrantSubject(self.guild_id, self.discord_role_id)

    @property
    def expiry(self) -> Instant | None:
        return None if self.expires_at is None else Instant.parse_iso(self.expires_at.isoformat())


class GrantValues(SubjectValues):
    pattern: str
    effect: Effect | None = None


class AssignmentValues(SubjectValues):
    role_id: int
    present: bool = True


class RankValues(PermissionValues):
    role_id: int
    rank: int = Field(ge=0, le=MAX_ROLE_RANK)


class PatternValues(PermissionValues):
    role_id: int
    pattern: str
    mode: int | None = None

    @model_validator(mode="after")
    def valid_mode(self) -> Self:
        if self.mode not in {None, PatternMode.INCLUDE, PatternMode.EXCLUDE}:
            raise ValidationError
        return self


class IncludeValues(PermissionValues):
    role_id: int
    included_id: int
    present: bool = True


@dataclass(slots=True)
class PermissionGraph:
    revision: int
    admin: PermissionAdministrationService
    roles: tuple[PermissionRole, ...]
    actor: UserActor | None = None

    def role(self, identity: int) -> PermissionRole:
        for role in self.roles:
            if role.id == identity:
                return role
        raise NotFoundError

    @property
    def current_actor(self) -> UserActor:
        assert self.actor is not None
        return self.actor


class PermissionFieldCommand[ValuesT: PermissionValues](
    FieldCommand[PermissionEditUnitOfWork, PermissionGraph, ValuesT]
):
    def __init__(self, admin: PermissionAdministrationService, values: type[ValuesT]) -> None:
        async def load(uow: PermissionEditUnitOfWork, command: EditCommand) -> PermissionGraph:
            revision = await uow.permission_admin.lock_graph()
            return PermissionGraph(revision, admin.in_transaction(uow), await uow.permission_admin.list_roles())

        async def authorize(uow: PermissionEditUnitOfWork, actor: Actor, root: PermissionGraph) -> None:
            root.actor = await root.admin.resolve_actor(context=ActingContext.for_actor(actor))

        async def flush(uow: PermissionEditUnitOfWork, root: PermissionGraph, expected: Revision) -> Revision:
            return Revision(PERMISSION_GRAPH, await uow.permissions.epoch())

        super().__init__(Aggregate(PERMISSION_GRAPH, load, lambda root: root.revision, flush), values, authorize)


class PermissionGrantEdit(PermissionFieldCommand[GrantValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, GrantValues)

    @override
    async def capture(self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand) -> GrantValues:
        values = GrantValues.model_validate_json(command.values.encoded)
        rules = await uow.permission_admin.list_rules(
            subject_account_id=values.account_id, subject_role_id=values.discord_role_id, guild_id=values.guild_id
        )
        for rule in rules:
            if rule.pattern == values.pattern and rule.scope_guild_id == values.scope_guild_id:
                return values.model_copy(
                    update={
                        "effect": Effect(rule.effect),
                        "reason": rule.reason,
                        "expires_at": None if rule.expires_at is None else rule.expires_at.to_stdlib(),
                    }
                )
        return values.model_copy(update={"effect": None, "reason": None, "expires_at": None})

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: GrantValues,
        after: GrantValues,
    ) -> GrantValues:
        actor = root.current_actor
        if after.effect is None:
            await root.admin.check_revoke(
                uow, actor, pattern=after.pattern, account_id=after.account_id, scope_guild_id=after.scope_guild_id
            )
            return after.model_copy(update={"reason": None, "expires_at": None})
        root.admin.check_grant(
            actor,
            pattern=after.pattern,
            effect=after.effect,
            scope_guild_id=after.scope_guild_id,
            reason=after.reason,
            subject=after.subject,
        )
        return after

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: GrantValues, after: GrantValues
    ) -> None:
        if after.effect is None:
            await root.admin.revoke(
                context=ActingContext.for_actor(root.current_actor),
                pattern=after.pattern,
                scope_guild_id=after.scope_guild_id,
                subject=after.subject,
            )
        else:
            await root.admin.grant(
                context=ActingContext.for_actor(root.current_actor),
                pattern=after.pattern,
                effect=after.effect,
                scope_guild_id=after.scope_guild_id,
                expires_at=after.expiry,
                reason=after.reason,
                subject=after.subject,
            )


class PermissionAssignmentEdit(PermissionFieldCommand[AssignmentValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, AssignmentValues)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> AssignmentValues:
        values = AssignmentValues.model_validate_json(command.values.encoded)
        assignments = await uow.permission_admin.list_assignments(
            subject_account_id=values.account_id, subject_role_id=values.discord_role_id, guild_id=values.guild_id
        )
        for assignment in assignments:
            if assignment.role_id == values.role_id and assignment.scope_guild_id == values.scope_guild_id:
                return values.model_copy(
                    update={
                        "present": True,
                        "reason": assignment.reason,
                        "expires_at": None if assignment.expires_at is None else assignment.expires_at.to_stdlib(),
                    }
                )
        return values.model_copy(update={"present": False, "reason": None, "expires_at": None})

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: AssignmentValues,
        after: AssignmentValues,
    ) -> AssignmentValues:
        role = root.role(after.role_id)
        if after.present:
            await root.admin.check_assignment(
                uow, root.current_actor, role, scope_guild_id=after.scope_guild_id, subject=after.subject
            )
            return after
        await root.admin.check_unassignment(uow, root.current_actor, role, after.scope_guild_id)
        return after.model_copy(update={"reason": None, "expires_at": None})

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: AssignmentValues, after: AssignmentValues
    ) -> None:
        role = root.role(after.role_id)
        if after.present:
            await root.admin.assign(
                role,
                context=ActingContext.for_actor(root.current_actor),
                scope_guild_id=after.scope_guild_id,
                expires_at=after.expiry,
                reason=after.reason,
                subject=after.subject,
            )
        else:
            await root.admin.unassign(
                role,
                context=ActingContext.for_actor(root.current_actor),
                scope_guild_id=after.scope_guild_id,
                subject=after.subject,
            )


class AssignmentBatch(PermissionValues):
    assignments: tuple[AssignmentValues, ...] = Field(min_length=1, max_length=25)

    @model_validator(mode="after")
    def distinct_targets(self) -> Self:
        if len({(a.account_id, a.discord_role_id) for a in self.assignments}) != len(self.assignments):
            raise ValidationError
        return self


class PermissionAssignmentBatchEdit(PermissionFieldCommand[AssignmentBatch]):
    """Capture each recipient's previous assignment under one graph lock for undo."""

    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, AssignmentBatch)
        self.single = PermissionAssignmentEdit(admin)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> AssignmentBatch:
        batch = AssignmentBatch.model_validate_json(command.values.encoded)
        return AssignmentBatch(
            assignments=tuple(
                [
                    await self.single.capture(uow, root, replace(command, values=item.edit_values()))
                    for item in batch.assignments
                ]
            )
        )

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: AssignmentBatch,
        after: AssignmentBatch,
    ) -> AssignmentBatch:
        return AssignmentBatch(
            assignments=tuple(
                [
                    await self.single.validate(uow, actor, root, command, old, new)
                    for old, new in zip(before.assignments, after.assignments, strict=True)
                ]
            )
        )

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: AssignmentBatch, after: AssignmentBatch
    ) -> None:
        for old, new in zip(before.assignments, after.assignments, strict=True):
            if old != new:
                await self.single.write(uow, root, old, new)


class PermissionRankEdit(PermissionFieldCommand[RankValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, RankValues)

    @override
    async def capture(self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand) -> RankValues:
        values = RankValues.model_validate_json(command.values.encoded)
        return RankValues(role_id=values.role_id, rank=root.role(values.role_id).rank)

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: RankValues,
        after: RankValues,
    ) -> RankValues:
        root.admin.check_rank(root.current_actor, root.role(after.role_id), after.rank)
        return after

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: RankValues, after: RankValues
    ) -> None:
        await root.admin.set_rank(
            root.role(after.role_id), after.rank, context=ActingContext.for_actor(root.current_actor)
        )


class PermissionPatternEdit(PermissionFieldCommand[PatternValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, PatternValues)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> PatternValues:
        values = PatternValues.model_validate_json(command.values.encoded)
        role = root.role(values.role_id)
        mode = (
            PatternMode.INCLUDE
            if values.pattern in role.includes
            else PatternMode.EXCLUDE
            if values.pattern in role.excludes
            else None
        )
        return values.model_copy(update={"mode": mode})

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: PatternValues,
        after: PatternValues,
    ) -> PatternValues:
        role = root.role(after.role_id)
        if after.mode is None:
            root.admin._require_role_management(root.current_actor, role)
        else:
            root.admin.check_pattern(root.current_actor, role, after.pattern, after.mode)
        return after

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: PatternValues, after: PatternValues
    ) -> None:
        role = root.role(after.role_id)
        if after.mode is None:
            await root.admin.remove_pattern(role, after.pattern, context=ActingContext.for_actor(root.current_actor))
        else:
            await root.admin.add_pattern(
                role, after.pattern, context=ActingContext.for_actor(root.current_actor), mode=after.mode
            )


class PermissionIncludeEdit(PermissionFieldCommand[IncludeValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, IncludeValues)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> IncludeValues:
        values = IncludeValues.model_validate_json(command.values.encoded)
        return values.model_copy(update={"present": values.included_id in root.role(values.role_id).includes_roles})

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: IncludeValues,
        after: IncludeValues,
    ) -> IncludeValues:
        role, included = root.role(after.role_id), root.role(after.included_id)
        if after.present:
            await root.admin.check_include(uow, root.current_actor, role, included)
        else:
            root.admin._require_role_management(root.current_actor, role)
        return after

    @override
    async def write(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, before: IncludeValues, after: IncludeValues
    ) -> None:
        role, included = root.role(after.role_id), root.role(after.included_id)
        if after.present:
            await root.admin.add_include(role, included, context=ActingContext.for_actor(root.current_actor))
        else:
            await root.admin.remove_include(role, included, context=ActingContext.for_actor(root.current_actor))


ROLE_CREATE = "permissions.role-create"
ROLE_DELETE = "permissions.role-delete"


class RolePresenceValues(PermissionValues):
    role_id: int
    slug: str = Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")
    name: str
    description: str | None = None
    guild_id: int | None = None
    rank: int = Field(default=0, ge=0, le=MAX_ROLE_RANK)
    present: bool = True

    @classmethod
    def capture(cls, role: PermissionRole) -> RolePresenceValues:
        return cls(
            role_id=role.id,
            slug=role.slug,
            name=role.name,
            description=role.description,
            guild_id=role.guild_id,
            rank=role.rank,
            present=not role.withdrawn,
        )


class PermissionRoleCreation(PermissionFieldCommand[RolePresenceValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, RolePresenceValues)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> RolePresenceValues:
        values = RolePresenceValues.model_validate_json(command.values.encoded)
        row = await uow.permission_admin.role_identity(values.role_id)
        return values.model_copy(update={"present": False}) if row is None else RolePresenceValues.capture(row)

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: RolePresenceValues,
        after: RolePresenceValues,
    ) -> RolePresenceValues:
        root.admin.check_role_creation(root.current_actor, rank=after.rank, guild_id=after.guild_id)
        row = await uow.permission_admin.role_identity(after.role_id)
        if row is None:
            if not after.present:
                raise NotFoundError
        elif (
            row.protected
            or row.builtin_key is not None
            or before.model_copy(update={"present": after.present}) != after
            or not await uow.permission_admin.unused_role(row.id)
        ):
            raise ConflictError
        if after.present and not await uow.permission_admin.role_slug_available(
            after.slug, after.guild_id, after.role_id
        ):
            raise ConflictError
        return after

    @override
    async def write(
        self,
        uow: PermissionEditUnitOfWork,
        root: PermissionGraph,
        before: RolePresenceValues,
        after: RolePresenceValues,
    ) -> None:
        row = await uow.permission_admin.role_identity(after.role_id)
        if row is None:
            row = PermissionRole(
                id=after.role_id,
                slug=after.slug,
                name=after.name,
                description=after.description,
                guild_id=after.guild_id,
                rank=after.rank,
                created_by_account_id=root.current_actor.account_id,
            )
        row.withdrawn = not after.present
        await uow.permission_admin.save_role_presence(
            row,
            audit=AuditEntry(
                action=AuditAction.ROLE_CREATE if after.present else AuditAction.ROLE_WITHDRAW,
                actor_account_id=root.current_actor.account_id,
                role_id=row.id,
                subject_guild_id=row.guild_id,
                details={"slug": row.slug, "rank": row.rank},
            ),
        )


class RoleDeletionValues(PermissionValues):
    role_id: int
    deleted: bool = True


class PermissionRoleDeletion(PermissionFieldCommand[RoleDeletionValues]):
    def __init__(self, admin: PermissionAdministrationService) -> None:
        super().__init__(admin, RoleDeletionValues)

    @override
    async def capture(
        self, uow: PermissionEditUnitOfWork, root: PermissionGraph, command: EditCommand
    ) -> RoleDeletionValues:
        values = RoleDeletionValues.model_validate_json(command.values.encoded)
        root.admin._require_role_management(root.current_actor, root.role(values.role_id))
        return values.model_copy(update={"deleted": False})

    @override
    async def validate(
        self,
        uow: PermissionEditUnitOfWork,
        actor: Actor,
        root: PermissionGraph,
        command: EditCommand,
        before: RoleDeletionValues,
        after: RoleDeletionValues,
    ) -> RoleDeletionValues:
        if not after.deleted:
            raise ValidationError
        return after

    @override
    async def prepare(
        self, uow: PermissionEditUnitOfWork, actor: Actor, command: EditCommand
    ) -> PreparedFields[PermissionGraph, RoleDeletionValues]:
        return replace(await super().prepare(uow, actor, command), final=True)

    @override
    async def write(
        self,
        uow: PermissionEditUnitOfWork,
        root: PermissionGraph,
        before: RoleDeletionValues,
        after: RoleDeletionValues,
    ) -> None:
        await root.admin.delete_role(root.role(after.role_id), context=ActingContext.for_actor(root.current_actor))

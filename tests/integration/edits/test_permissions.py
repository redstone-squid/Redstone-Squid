"""Permission history preserves policy and audits inside the shared graph transaction."""

from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ConflictError, InvalidStateError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand, Revision, RevisionConflictError
from squid.permissions.application.administration import PermissionAdministrationService
from squid.permissions.application.edits import (
    ASSIGNMENT_EDIT,
    GRANT_EDIT,
    INCLUDE_EDIT,
    PATTERN_EDIT,
    PERMISSION_GRAPH,
    RANK_EDIT,
    ROLE_CREATE,
    ROLE_DELETE,
    AssignmentValues,
    GrantValues,
    IncludeValues,
    PatternValues,
    PermissionAssignmentEdit,
    PermissionGrantEdit,
    PermissionIncludeEdit,
    PermissionPatternEdit,
    PermissionRankEdit,
    PermissionRoleCreation,
    PermissionRoleDeletion,
    PermissionValues,
    RankValues,
    RoleDeletionValues,
    RolePresenceValues,
)
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import Effect
from squid.permissions.domain.catalogue import PERM_NODE_VIEW, ROLE_DEFINITION_MANAGE
from squid.permissions.models import PermissionAuditEntry
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.unit_of_work import UnitOfWorkFactory


async def setup(
    sessions: async_sessionmaker[AsyncSession],
) -> tuple[
    UnitOfWorkFactory[SqlAlchemyUnitOfWork],
    PermissionAdministrationService,
    EditExecutor[SqlAlchemyUnitOfWork],
    Actor,
    int,
]:
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    admin = PermissionAdministrationService(PermissionService(uow_factory=factory), uow_factory=factory)
    executor = EditExecutor(
        factory,
        {
            GRANT_EDIT: PermissionGrantEdit(admin),
            ASSIGNMENT_EDIT: PermissionAssignmentEdit(admin),
            RANK_EDIT: PermissionRankEdit(admin),
            PATTERN_EDIT: PermissionPatternEdit(admin),
            INCLUDE_EDIT: PermissionIncludeEdit(admin),
            ROLE_CREATE: PermissionRoleCreation(admin),
            ROLE_DELETE: PermissionRoleDeletion(admin),
        },
    )
    async with sessions.begin() as session:
        owner, subject = Account(), Account()
        session.add_all((owner, subject))
        await session.flush()
    return factory, admin, executor, Actor(account_id=owner.id, is_bot_owner=True), subject.id


async def command(admin: PermissionAdministrationService, kind: str, values: PermissionValues) -> EditCommand:
    return EditCommand(kind, (Revision(PERMISSION_GRAPH, await admin.revision()),), values.edit_values())


async def test_permission_commands_undo_redo_and_final_role_deletion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory, admin, executor, owner, subject = await setup(migrated_session_factory)
    role_id = await admin.reserve_role_identity()
    created = await executor.execute(
        await command(
            admin, ROLE_CREATE, RolePresenceValues(role_id=role_id, slug="history-role", name="History role", rank=1)
        ),
        owner,
    )
    assert created.receipt is not None
    assert (await admin.role("history-role")).id == role_id
    undone = await executor.execute(created.receipt.undo(created.outcome.revisions), owner)
    assert undone.receipt is not None
    assert role_id not in {role.id for role in await admin.roles()}
    restored = await executor.execute(created.receipt.redo(undone.outcome.revisions), owner)
    assert restored.receipt is not None
    assert (await admin.role("history-role")).id == role_id
    included = await admin.create_role(owner, slug="included-role", name="Included role", rank=0)
    changes: tuple[tuple[str, PermissionValues], ...] = (
        (RANK_EDIT, RankValues(role_id=role_id, rank=10)),
        (PATTERN_EDIT, PatternValues(role_id=role_id, pattern=PERM_NODE_VIEW.name, mode=1)),
        (INCLUDE_EDIT, IncludeValues(role_id=role_id, included_id=included.id)),
        (GRANT_EDIT, GrantValues(account_id=subject, pattern=PERM_NODE_VIEW.name, effect=Effect.ALLOW)),
        (ASSIGNMENT_EDIT, AssignmentValues(role_id=role_id, account_id=subject)),
    )
    last = restored
    for kind, values in changes:
        saved_command = await command(admin, kind, values)
        saved = await executor.execute(saved_command, owner)
        assert saved.receipt is not None
        assert (await executor.execute(saved_command, owner)).replayed
        reversed_result = await executor.execute(saved.receipt.undo(saved.outcome.revisions), owner)
        assert reversed_result.receipt is not None
        last = await executor.execute(saved.receipt.redo(reversed_result.outcome.revisions), owner)
        assert last.receipt is not None
        assert last.receipt.after == saved.receipt.after
        assert await admin.revision() == last.outcome.revisions[0].value
    with pytest.raises(ConflictError):
        await executor.execute(restored.receipt.undo(last.outcome.revisions), owner)
    assert last.receipt is not None
    async with migrated_session_factory() as session:
        audits = await session.scalar(select(func.count()).select_from(PermissionAuditEntry))
    noop = await executor.execute(await command(admin, ASSIGNMENT_EDIT, changes[-1][1]), owner)
    assert not noop.outcome.changed
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(PermissionAuditEntry)) == audits
    await admin.set_rank(owner, await admin.role("history-role"), 11)
    with pytest.raises(RevisionConflictError):
        await executor.execute(last.receipt.undo(last.outcome.revisions), owner)
    deleted = await executor.execute(await command(admin, ROLE_DELETE, RoleDeletionValues(role_id=role_id)), owner)
    assert deleted.receipt is not None
    assert deleted.receipt.final
    with pytest.raises(InvalidStateError):
        deleted.receipt.undo(deleted.outcome.revisions)
    assert role_id not in {role.id for role in await admin.roles()}
    assert await admin.assignments_for(account_id=subject) == ()


async def test_permission_commands_resolve_current_rank_and_authority_even_for_noops(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory, admin, executor, owner, subject = await setup(migrated_session_factory)
    guardian = await admin.create_role(owner, slug="guardian-role", name="Guardian", rank=100)
    target = await admin.create_role(owner, slug="editable-role", name="Editable", rank=1)
    await admin.assign(owner, guardian, account_id=subject)
    await admin.grant(owner, pattern=ROLE_DEFINITION_MANAGE.name, account_id=subject)
    actor = Actor(account_id=subject)
    saved = await executor.execute(await command(admin, RANK_EDIT, RankValues(role_id=target.id, rank=10)), actor)
    assert saved.receipt is not None
    with pytest.raises(AuthorizationError):
        await executor.execute(await command(admin, RANK_EDIT, RankValues(role_id=target.id, rank=100)), actor)
    await admin.revoke(owner, pattern=ROLE_DEFINITION_MANAGE.name, account_id=subject)
    current = (Revision(PERMISSION_GRAPH, await admin.revision()),)
    with pytest.raises(AuthorizationError):
        await executor.execute(saved.receipt.undo(current), actor)
    with pytest.raises(AuthorizationError):
        await executor.execute(await command(admin, RANK_EDIT, RankValues(role_id=target.id, rank=10)), actor)

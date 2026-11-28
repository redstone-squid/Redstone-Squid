"""Permission writes, audit records, and cache visibility across transaction boundaries."""

from functools import partial

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.permissions.application import PermissionEpochWatcher, PermissionService, SubjectRuleCache
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.application.ports import AuditEntry
from squid.core.permissions.domain.models import (
    AuditAction,
    AuditSubjectKind,
    ExternalRoleGrantSubject,
    PatternMode,
    PermissionSubjectFilters,
    RolePermissionSubject,
)
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork


@pytest.mark.parametrize("commit", [False, True])
async def test_permission_writes_audit_and_epoch_share_the_callers_transaction(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    commit: bool,
) -> None:
    async with migrated_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    cache = SubjectRuleCache()
    permissions = PermissionService(uow_factory=factory, cache=cache)
    subject = UserActor(account_id=account_id)
    node = "build.submission.approve"
    assert not (await permissions.check(subject, node)).allowed
    async with factory() as uow:
        epoch = await uow.permissions.epoch()
        await uow.permission_admin.upsert_grant(
            pattern=node,
            effect=1,
            subject_account_id=account_id,
            subject_role_id=None,
            subject_guild_id=None,
            scope_guild_id=None,
            expires_at=None,
            granted_by_account_id=None,
            reason=None,
            audit=AuditEntry(
                action=AuditAction.GRANT, subject_kind=AuditSubjectKind.ACCOUNT, subject_id=account_id, pattern=node
            ),
        )
        assert (await permissions.check(subject, *[node], uow=uow)).allowed_nodes == frozenset({node})
        assert len((await uow.permission_admin.list_audit()).items) == 1
        if commit:
            await uow.commit()

    # Transaction-local authorization must not replace cached committed rules.
    assert not (await permissions.check(subject, node)).allowed
    async with factory() as uow:
        assert (
            bool(
                await uow.permission_admin.list_rules(
                    filters=PermissionSubjectFilters.for_subject(account_id=account_id)
                )
            )
            == commit
        )
        assert bool((await uow.permission_admin.list_audit()).items) == commit
        assert (await uow.permissions.epoch() > epoch) == commit
    await PermissionEpochWatcher(cache, uow_factory=factory).refresh()
    assert (await permissions.check(subject, node)).allowed == commit


async def test_role_creation_commits_with_audit_and_rolls_back_if_audit_fails(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    permissions = PermissionService(uow_factory=factory)
    admin = PermissionAdministrationService(permissions, uow_factory=factory)
    actor = await admin.resolve_actor(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True)))
    role = await admin.create_role(context=ActingContext.direct(actor), slug="uow-reviewer", name="Reviewer", rank=3)
    assert role.id > 0
    assert (
        await admin.role("uow-reviewer", context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True)))
    ).id == role.id
    assert (await admin.audit(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True))))[
        0
    ].role_id == role.id

    async with factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.permission_admin.create_role(
                slug="abandoned",
                name="Abandoned",
                description=None,
                guild_id=None,
                rank=0,
                created_by_account_id=None,
                # The missing audit actor fails after role insertion, testing rollback of both writes.
                audit=AuditEntry(action=AuditAction.ROLE_CREATE, actor_account_id=-1),
            )
    async with factory() as uow:
        assert await uow.permission_admin.get_role("abandoned") is None
        assert len((await uow.permission_admin.list_audit()).items) == 1


async def test_role_aggregation_preserves_empty_roles_and_independent_pattern_edge_lists(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    admin = PermissionAdministrationService(PermissionService(uow_factory=factory), uow_factory=factory)
    actor = await admin.resolve_actor(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True)))
    empty = await admin.create_role(context=ActingContext.direct(actor), slug="empty", name="Empty", rank=1)
    child = await admin.create_role(context=ActingContext.direct(actor), slug="child", name="Child", rank=1)
    parent = await admin.create_role(context=ActingContext.direct(actor), slug="parent", name="Parent", rank=2)
    await admin.add_pattern(parent, "build.**", context=ActingContext.direct(actor))
    await admin.add_pattern(parent, "vote.**", context=ActingContext.direct(actor))
    await admin.add_pattern(parent, "@destructive", context=ActingContext.direct(actor), mode=PatternMode.EXCLUDE)
    await admin.add_include(parent, empty, context=ActingContext.direct(actor))
    await admin.add_include(parent, child, context=ActingContext.direct(actor))
    roles = {
        role.id: role
        for role in await admin.roles(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True)))
    }
    assert roles[empty.id].includes == ()
    assert roles[empty.id].includes_roles == ()
    assert roles[parent.id].includes == ("build.**", "vote.**")
    assert roles[parent.id].excludes == ("@destructive",)
    assert roles[parent.id].includes_roles == tuple(sorted((empty.id, child.id)))


async def test_tagged_role_subject_retains_guild_identity_and_audit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    permissions = PermissionService(uow_factory=factory)
    admin = PermissionAdministrationService(permissions, uow_factory=factory)
    owner = await admin.resolve_actor(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True)))
    await admin.grant(
        context=ActingContext.direct(owner),
        subject=ExternalRoleGrantSubject(100, 200),
        pattern="settings.server.edit",
        scope_guild_id=100,
    )
    assert (
        await permissions.check(
            RolePermissionSubject(guild_id=100, discord_role_ids=frozenset({200})), "settings.server.edit"
        )
    ).allowed
    assert not (
        await permissions.check(
            RolePermissionSubject(guild_id=101, discord_role_ids=frozenset({200})), "settings.server.edit"
        )
    ).allowed
    entry = (await admin.audit(context=ActingContext.direct(UserActor(discord_user_id=1, is_bot_owner=True))))[0]
    assert entry.subject_kind == "discord_role"
    assert entry.subject_id == 200
    assert entry.subject_guild_id == 100

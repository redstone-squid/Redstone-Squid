"""Permission writes, audit records, and cache visibility across transaction boundaries."""

from functools import partial

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.permissions.application import PermissionEpochWatcher, PermissionService, SubjectRuleCache
from squid.permissions.application.administration import PermissionAdministrationService
from squid.permissions.application.ports import AuditEntry
from squid.permissions.domain import Subject
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


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
    subject = Subject(account_id=account_id)
    node = "build.submission.approve"
    assert not await permissions.allows(subject, node)
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
            audit=AuditEntry(action="grant", subject_kind="account", subject_id=account_id, pattern=node),
        )
        assert await permissions.capabilities_in(uow, subject, [node]) == frozenset({node})
        assert len(await uow.permission_admin.list_audit()) == 1
        if commit:
            await uow.commit()

    # Transaction-local authorization must not replace cached committed rules.
    assert not await permissions.allows(subject, node)
    async with factory() as uow:
        assert bool(await uow.permission_admin.list_rules(subject_account_id=account_id)) == commit
        assert bool(await uow.permission_admin.list_audit()) == commit
        assert (await uow.permissions.epoch() > epoch) == commit
    await PermissionEpochWatcher(cache, uow_factory=factory).refresh()
    assert await permissions.allows(subject, node) == commit


async def test_role_creation_commits_with_audit_and_rolls_back_if_audit_fails(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    permissions = PermissionService(uow_factory=factory)
    admin = PermissionAdministrationService(permissions, uow_factory=factory)
    actor = await admin.actor(Subject(is_bot_owner=True))
    role = await admin.create_role(actor, slug="uow-reviewer", name="Reviewer", rank=3)
    assert role.id > 0
    assert (await admin.role("uow-reviewer")).id == role.id
    assert (await admin.audit())[0].role_id == role.id

    async with factory() as uow:
        with pytest.raises(IntegrityError):
            await uow.permission_admin.create_role(
                slug="abandoned",
                name="Abandoned",
                description=None,
                guild_id=None,
                rank=0,
                created_by_account_id=None,
                audit=AuditEntry(action="role_create", actor_account_id=-1),
            )
    async with factory() as uow:
        assert await uow.permission_admin.get_role("abandoned") is None
        assert len(await uow.permission_admin.list_audit()) == 1

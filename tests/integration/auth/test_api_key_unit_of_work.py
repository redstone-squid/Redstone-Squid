"""API-key authorization shares the credential transaction."""

from functools import partial

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.models import Account
from squid.auth.application import ApiKeyService
from squid.auth.models.entities import ApiKey
from squid.core.errors import AuthorizationError
from squid.permissions.application import PermissionService, SubjectRuleCache
from squid.permissions.application.ports import AuditEntry
from squid.permissions.domain import Subject
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_issuance_checks_current_authority_despite_a_cached_grant(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as session:
        account = Account()
        session.add(account)
        await session.flush()
        account_id = account.id
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    permissions = PermissionService(uow_factory=factory, cache=SubjectRuleCache())
    api_keys = ApiKeyService("test-pepper", uow_factory=factory, permissions=permissions)
    subject = Subject(account_id=account_id)
    node = "build.submission.approve"
    async with factory() as uow:
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
        await uow.commit()
    assert await permissions.allows(subject, node)
    issued = await api_keys.issue(label="Before revocation", scopes=[node], owner_account_id=account_id)
    assert issued.key.id > 0

    async with factory() as uow:
        await uow.permission_admin.delete_grant(
            pattern=node,
            subject_account_id=account_id,
            subject_role_id=None,
            scope_guild_id=None,
            audit=AuditEntry(action="revoke", subject_kind="account", subject_id=account_id, pattern=node),
        )
        await uow.commit()
    # Keep the cache stale: key issuance must read authority inside its own transaction.
    assert await permissions.allows(subject, node)
    with pytest.raises(AuthorizationError):
        await api_keys.issue(label="After revocation", scopes=[node], owner_account_id=account_id)
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApiKey)) == 1

    authenticated = await api_keys.authenticate(issued.token, used_ip="192.0.2.1")
    assert authenticated is not None
    assert authenticated.last_used_at is not None
    assert str(authenticated.last_used_ip) == "192.0.2.1"
    # Repeated authentication within the write-throttle interval preserves the previous usage sample.
    again = await api_keys.authenticate(issued.token, used_ip="192.0.2.2")
    assert again is not None
    assert again.last_used_at == authenticated.last_used_at
    assert str(again.last_used_ip) == "192.0.2.1"


async def test_failed_commit_rolls_back_the_flushed_key_before_returning_a_secret(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingCommit(SqlAlchemyUnitOfWork):
        async def commit(self) -> None:
            raise RuntimeError("commit failed")

    api_keys = ApiKeyService("test-pepper", uow_factory=partial(FailingCommit, migrated_session_factory))
    with pytest.raises(RuntimeError, match="commit failed"):
        await api_keys.issue(label="Abandoned", scopes=["build.submission.read"])
    async with migrated_session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(ApiKey)) == 0

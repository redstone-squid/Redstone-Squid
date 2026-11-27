"""Impersonated operations keep ordinary ownership and durable operator attribution."""

from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from whenever import Instant

from squid.core.accounts.domain import PrivacyNoticeReceipt
from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.auth.impersonation import ImpersonationService
from squid.core.auth.models.sessions import ImpersonatedEdit, ImpersonationSession, ImpersonationUse
from squid.core.edits.domain import EditOutcome, EditTarget, Revision
from squid.core.errors import AuthorizationError
from squid.core.permissions.application import PermissionService
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork


async def test_session_binding_revocation_and_transaction_audit(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory.begin() as db:
        operator_account = Account()
        target = Account()
        db.add_all((operator_account, target))
    factory = partial(SqlAlchemyUnitOfWork, migrated_session_factory, verification_code_pepper="test-pepper")
    service = ImpersonationService(factory, PermissionService(uow_factory=factory))
    operator = UserActor(account_id=operator_account.id, is_bot_owner=True)
    guild_admin = UserActor(account_id=operator_account.id, guild_id=1, discord_guild_admin=True)
    with pytest.raises(AuthorizationError):
        await service.start("discord:1", target.id, "check draft", context=ActingContext.direct(guild_admin))
    session = await service.start("discord:1", target.id, "check draft", context=ActingContext.direct(operator))
    for caller, binding in (
        (operator, "discord:2"),
        (UserActor(account_id=target.id, is_bot_owner=True), "discord:1"),
        (guild_admin, "discord:1"),
    ):
        with pytest.raises(AuthorizationError):
            await service.use(binding, session.id, "draft", context=ActingContext.direct(caller))

    resolved = await service.use("discord:1", session.id, "draft", context=ActingContext.direct(operator))
    account, use_id = resolved.target, resolved.use_id
    assert account.id == target.id
    effective = UserActor(account_id=account.id, audit_use_id=use_id)
    edit_id = uuid4()
    with effective.audit_scope():
        async with factory() as uow:
            await uow.accounts.update_consent(effective.require_account_id(), PrivacyNoticeReceipt.grant_current())
            await uow.edits.record(
                EditOutcome(
                    edit_id,
                    effective.key,
                    bytes(32),
                    (Revision(EditTarget("account", str(account.id)), 1),),
                    changed=True,
                    final=True,
                )
            )
            await uow.commit()
        async with factory() as uow:
            await uow.accounts.update_consent(operator.require_account_id(), PrivacyNoticeReceipt.grant_current())
    async with migrated_session_factory() as db:
        assert (await db.get_one(ImpersonatedEdit, edit_id)).use_id == use_id
        assert (await db.get_one(Account, target.id)).privacy_notice_version is not None
        assert (await db.get_one(Account, operator_account.id)).privacy_notice_version is None
        assert await db.scalar(select(ImpersonationUse.commits).where(ImpersonationUse.id == use_id)) == 1

    await service.stop("discord:1", session.id, context=ActingContext.direct(operator))
    with pytest.raises(AuthorizationError):
        await service.use("discord:1", session.id, "stale button", context=ActingContext.direct(operator))
    async with migrated_session_factory.begin() as db:
        expired = await db.get_one(ImpersonationSession, session.id)
        expired.revoked_at = None
        expired.expires_at = Instant.now().subtract(seconds=1)
    with pytest.raises(AuthorizationError):
        await service.use("discord:1", session.id, "expired button", context=ActingContext.direct(operator))

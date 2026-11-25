"""Poll mutations read current account grants inside their writing transaction."""

from dataclasses import replace
from functools import partial

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, Actor
from squid.core.errors import AuthorizationError
from squid.core.permissions.models import PermissionGrant
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.core.voting.application import VoteService
from squid.core.voting.domain import PollScope, VoteRejection, VoteStatus, VoteVisibility
from tests.support.voting import GENERIC_OPTIONS


async def test_poll_creation_and_closure_recheck_grants_and_credential_scope(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    service = VoteService(uow_factory=partial(SqlAlchemyUnitOfWork, sessions))
    async with sessions.begin() as session:
        creator, moderator = Account(), Account()
        session.add_all([creator, moderator])
        await session.flush()
        session.add(PermissionGrant(pattern="vote.poll.create", effect=1, subject_account_id=creator.id))
        session.add(PermissionGrant(pattern="vote.poll.close_any", effect=1, subject_account_id=moderator.id))
    operator = Actor(account_id=creator.id, guild_id=10, capabilities=frozenset({"vote.poll.network_create"}))
    options = tuple(replace(option, guild_id=None) for option in GENERIC_OPTIONS)

    async def create(context: ActingContext, scope: PollScope) -> int:
        return await service.create_generic_poll(
            context=context,
            question="Ready?",
            visibility=VoteVisibility.VISIBLE_LIVE,
            duration_seconds=3600,
            guild_id=10,
            scope=scope,
            options=options,
        )

    with pytest.raises(AuthorizationError):
        await create(ActingContext.direct(operator), PollScope.NETWORK)
    with pytest.raises(AuthorizationError):
        await create(
            ActingContext.direct(replace(operator, credential_scopes=frozenset({"vote.poll.cast"}))), PollScope.GUILD
        )
    session_id = await create(ActingContext.direct(operator), PollScope.GUILD)
    reviewer = Actor(account_id=moderator.id, guild_id=10, capabilities=frozenset({"vote.poll.close_any"}))
    async with sessions.begin() as session:
        await session.execute(delete(PermissionGrant).where(PermissionGrant.subject_account_id == moderator.id))
    refusal = await service.close(session_id, context=ActingContext.direct(reviewer))
    assert refusal.rejection is VoteRejection.NOT_AUTHORIZED
    assert refusal.session is not None
    assert refusal.session.status is VoteStatus.OPEN

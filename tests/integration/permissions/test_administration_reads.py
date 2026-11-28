"""Permission administration addressed by stored row, as the HTTP API uses it."""

from functools import partial

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.models import Account
from squid.core.actors import ActingContext, UserActor
from squid.core.errors import NotFoundError, ServiceUnavailableError
from squid.core.pagination import FIRST_PAGE
from squid.core.permissions.application import PermissionService
from squid.core.permissions.application.administration import PermissionAdministrationService
from squid.core.permissions.domain import Reason
from squid.core.permissions.domain.models import AccountGrantSubject, ExternalRoleGrantSubject, PermissionSubjectFilters
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork

OWNER = ActingContext.direct(UserActor(account_id=None, discord_user_id=1, is_bot_owner=True))
NODE = "build.submission.approve"


async def account(sessions: async_sessionmaker[AsyncSession]) -> int:
    async with sessions.begin() as session:
        row = Account()
        session.add(row)
        await session.flush()
        return row.id


def administration(sessions: async_sessionmaker[AsyncSession]) -> PermissionAdministrationService:
    factory = partial(SqlAlchemyUnitOfWork, sessions)
    return PermissionAdministrationService(PermissionService(uow_factory=factory), uow_factory=factory)


async def test_a_stored_grant_is_explained_and_revoked_by_its_id(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin = administration(migrated_session_factory)
    subject = AccountGrantSubject(await account(migrated_session_factory))
    await admin.grant(context=OWNER, subject=subject, pattern=NODE)
    (grant,) = await admin.rules_for(context=OWNER, filters=PermissionSubjectFilters(account_id=subject.account_id))

    (decision,) = await admin.explain(subject, context=OWNER, nodes=(NODE,))
    assert decision.allowed
    assert decision.reason is Reason.RULE
    assert decision.decisive_rule is not None

    await admin.revoke_stored(grant.id, context=OWNER)
    assert await admin.rules_for(context=OWNER, filters=PermissionSubjectFilters(account_id=subject.account_id)) == ()
    with pytest.raises(NotFoundError):
        await admin.revoke_stored(grant.id, context=OWNER)


async def test_an_assignment_is_removed_by_its_id(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    admin = administration(migrated_session_factory)
    role = await admin.create_role(context=OWNER, slug="api-reviewer", name="Reviewer", rank=3)
    subject = ExternalRoleGrantSubject(guild_id=10, discord_role_id=20)
    await admin.assign(await admin.role_by_id(role.id, context=OWNER), context=OWNER, subject=subject)
    (assignment,) = await admin.assignments_for(context=OWNER, filters=PermissionSubjectFilters(discord_role_id=20))

    await admin.unassign_stored(assignment.id, context=OWNER)

    assert await admin.assignments_for(context=OWNER, filters=PermissionSubjectFilters(discord_role_id=20)) == ()


async def test_explaining_an_account_in_a_guild_needs_discord(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    admin = administration(migrated_session_factory)

    with pytest.raises(ServiceUnavailableError):
        await admin.explain(AccountGrantSubject(await account(migrated_session_factory)), context=OWNER, guild_id=10)


async def test_audit_anchors_page_newest_first(migrated_session_factory: async_sessionmaker[AsyncSession]) -> None:
    admin = administration(migrated_session_factory)
    subject = AccountGrantSubject(await account(migrated_session_factory))
    for pattern in ("build.**", "tag.**", "record.**"):
        await admin.grant(context=OWNER, subject=subject, pattern=pattern)

    first = await admin.audit_page(context=OWNER, page_size=2, selector=FIRST_PAGE)
    assert [entry.pattern for entry in first.items] == ["record.**", "tag.**"]
    assert first.total == 3
    assert first.next is not None
    rest = await admin.audit_page(context=OWNER, page_size=2, selector=first.next.selector())
    assert [entry.pattern for entry in rest.items] == ["build.**"]

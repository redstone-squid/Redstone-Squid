"""Counting the people credited across the public catalogue."""

from functools import partial

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.domain import IdentityProvider
from squid.core.accounts.models import Account, AccountIdentity
from squid.core.builds.domain import BuildStatus
from squid.core.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole
from squid.core.builds.models import BuildCredit, OtherBuild
from squid.core.persistence.unit_of_work import SqlAlchemyUnitOfWork
from tests.support.build_edits import build_edit_session
from tests.support.build_edits import transact_build_edit as transact

LEGACY = CreditAttribution.LEGACY


async def test_creators_count_each_person_on_approved_builds_once(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    repository = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    async with build_edit_session(migrated_session_factory) as session:
        eve = Account()
        session.add(eve)
        await session.flush()
        session.add(AccountIdentity(account_id=eve.id, provider=IdentityProvider.DISCORD, subject="42"))
        eve_id = eve.id

    credits = {
        BuildStatus.APPROVED: [
            [
                Credit(name="Alice"),
                Credit(name="Bob", role=CreditRole.DESIGNER),
                Credit(name="Carol", role=CreditRole.CONTRIBUTOR),
            ],
            [
                Credit(name="alice"),
                Credit(name="Eve", attribution=LEGACY),
                Credit(name="Eve Alt", attribution=LEGACY),
                Credit(name="eve#0", kind=CreditKind.DISCORD, subject="42"),
            ],
        ],
        BuildStatus.PENDING: [[Credit(name="Dave")]],
    }
    for status, builds in credits.items():
        for build_credits in builds:
            build = OtherBuild(
                submission_status=status, submitter_account_id=eve_id, credits=build_credits, ai_generated=False
            )
            await transact(repository, lambda uow, build=build: uow.builds.save(build), commit=True)
    async with build_edit_session(migrated_session_factory) as session:
        await session.execute(
            update(BuildCredit)
            .where(BuildCredit.name.in_(("Eve", "Eve Alt")))
            .values(account_id=eve_id, account_source="reviewed")
        )

    # Alice under two spellings, Bob, and Eve under two claimed names and a Discord identity.
    assert await transact(repository, lambda uow: uow.builds.count_creators()) == 3

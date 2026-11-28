"""Published build credits for attribution integration tests."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.accounts.domain import fold_creator_name
from squid.core.accounts.models import Account, CreatorAlias
from squid.core.builds.domain import BuildStatus
from squid.core.builds.models import BuildCredit, OtherBuild
from tests.support.build_edits import build_edit_session


async def publish_credits(
    sessions: async_sessionmaker[AsyncSession], names: Sequence[str], *, owner: int | None = None
) -> list[BuildCredit]:
    async with build_edit_session(sessions) as session:
        submitter = Account()
        session.add(submitter)
        await session.flush()
        build = OtherBuild(
            submission_status=BuildStatus.APPROVED, submitter_account_id=submitter.id, ai_generated=False
        )
        keys = [fold_creator_name(name) for name in names]
        aliases = {
            alias.normalized_name: alias
            for alias in await session.scalars(select(CreatorAlias).where(CreatorAlias.normalized_name.in_(keys)))
        }
        for name, key in zip(names, keys, strict=True):
            if key not in aliases:
                aliases[key] = CreatorAlias(name=name)
                session.add(aliases[key])
        await session.flush()
        build.build_credits = [
            BuildCredit(position=i, name=name.strip(), alias_id=aliases[key].id, account_id=owner)
            for i, (name, key) in enumerate(zip(names, keys, strict=True))
        ]
        session.add(build)
        await session.flush()
        return list(build.build_credits)

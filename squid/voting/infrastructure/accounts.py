"""Account-to-Discord-snowflake lookup for the Discord vote actor resolver."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.accounts.domain import IdentityProvider
from squid.accounts.infrastructure.models import AccountIdentity


class PostgresVoterDiscordIdLookup:
    """Read the Discord snowflake a voting account is reachable at, if any.

    Ballots store an `account_id` and only the Discord transport needs the snowflake, so it is read
    here rather than denormalized onto every ballot. `None` for an account with no Discord identity
    is an answer, not an error: such an account is not a guild member and carries no role weight.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def discord_id_for(self, account_id: int) -> int | None:
        async with self._session_factory() as session:
            subject = await session.scalar(
                select(AccountIdentity.subject).where(
                    AccountIdentity.account_id == account_id,
                    AccountIdentity.provider == IdentityProvider.DISCORD,
                )
            )
            return None if subject is None else int(subject)

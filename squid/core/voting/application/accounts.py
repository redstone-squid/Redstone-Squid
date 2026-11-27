"""Account lookups that vote adapters use to connect Discord users and accounts."""

from squid.core.accounts.application import AccountService
from squid.core.accounts.domain import IdentityProvider


class DiscordVoterLookup:
    """Find the account a reacting Discord user votes with."""

    def __init__(self, accounts: AccountService) -> None:
        self._accounts = accounts

    async def voter_account_id(self, discord_id: int) -> int | None:
        account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(discord_id))
        # ORM identity columns may still be None before flush.
        if (
            account is None or account.id is None  # pyright: ignore[reportUnnecessaryComparison]
        ):
            return None
        return account.id

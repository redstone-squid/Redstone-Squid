"""Account identities used by the Discord vote actor resolver."""

from squid.accounts.application import AccountService
from squid.accounts.domain import IdentityProvider


class AccountVoterDiscordIdLookup:
    """Resolve a voting account through the account application service."""

    def __init__(self, accounts: AccountService) -> None:
        self._accounts = accounts

    async def discord_id_for(self, account_id: int) -> int | None:
        account = await self._accounts.get_account_by_id(account_id)
        if account is None:
            return None
        identity = next(iter(account.identities_for(IdentityProvider.DISCORD)), None)
        return None if identity is None else identity.discord_id

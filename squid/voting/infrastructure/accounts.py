"""Account lookups that vote adapters use to connect Discord users and accounts."""

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


class AccountConsentedVoterLookup:
    """Find the consented account a reacting Discord user votes with."""

    def __init__(self, accounts: AccountService) -> None:
        self._accounts = accounts

    async def consented_account_id(self, discord_id: int) -> int | None:
        account = await self._accounts.get_account_by_identity(IdentityProvider.DISCORD, str(discord_id))
        if account is None or account.id is None or account.needs_consent_refresh:
            return None
        return account.id

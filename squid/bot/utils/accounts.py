"""Create Discord account identities for bot actions that need a persisted account.

Permission checks use the read-only `AccountIdCache` instead.
"""

import discord

from squid.accounts.application.services import AccountService
from squid.accounts.domain import IdentityProvider


async def get_or_create_discord_account_id(
    accounts: AccountService, user: discord.User | discord.Member | discord.abc.User | int
) -> int:
    """Return a Discord user's account ID, creating the identity if needed.

    Accept a user object or a snowflake. This helper does not ask for consent;
    callers that require it must complete the consent flow separately.
    """
    discord_id = user if isinstance(user, int) else user.id
    account = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(discord_id))
    assert account.id is not None, "get_or_create_identity always returns a persisted account"
    return account.id

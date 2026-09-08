"""Discord spelling of `AccountService` lookups.

`AccountService` is keyed on `(provider, subject)` so the CLI and Minecraft transports can share it;
the Discord shortcut lives here rather than on the service.
"""

import discord

from squid.accounts.application.services import AccountService
from squid.accounts.domain import IdentityProvider


async def account_id_for(accounts: AccountService, user: discord.User | discord.Member | discord.abc.User | int) -> int:
    """Return the account id behind a Discord user, creating the account on first sight.

    Only call this from a gateway event, which is evidence the snowflake acted; a permission check merely
    observes one and must read through `AccountIdCache` instead. A bare snowflake is accepted for the vote
    call sites, which hold an id rather than a user.
    """
    discord_id = user if isinstance(user, int) else user.id
    account = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(discord_id))
    assert account.id is not None, "get_or_create_identity always returns a persisted account"
    return account.id

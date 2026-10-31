"""Discord-side account conveniences.

`AccountService` is keyed on `(provider, subject)` because a caller may arrive over
Discord, the CLI, or a Minecraft server. The bot is the one transport that genuinely
always holds a Discord identity, so the Discord spelling of "get me an account id" lives
here rather than widening the service back out.
"""

import discord

from squid.accounts.application.services import AccountService
from squid.accounts.domain import IdentityProvider


async def account_id_for(accounts: AccountService, user: discord.User | discord.Member | discord.abc.User | int) -> int:
    """The account id behind a Discord user, created on first sight.

    A gateway event is evidence of the snowflake it carries, which is what makes creating
    an account here legitimate — unlike a permission check, which only observes one and so
    must go through `AccountIdCache` instead.

    A bare snowflake is accepted for the vote call sites, which are handed an id rather
    than a user object.
    """
    discord_id = user if isinstance(user, int) else user.id
    account = await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(discord_id))
    assert account.id is not None, "get_or_create_identity always returns a persisted account"
    return account.id

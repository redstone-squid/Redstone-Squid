"""The bot's stateless control routes, and the router that dispatches them.

These are the controls that must still work on a card posted by a past run of the bot:
the id encodes what the click refers to and the database owns the rest, so no session,
generation, or in-memory handler is involved. Route constants live here rather than beside
their handlers so the card that draws a button and the code that answers it cannot drift.

Handlers register themselves with `ROUTER` from their own modules; `RedstoneSquid` installs
the router once every extension has loaded, which is what freezes the table.
"""

import discord

import squid_layouts as sl

POLL_CLOSE = sl.Route("poll:close")
POLL_REFRESH = sl.Route("poll:refresh")
BUILD_EDIT = sl.Route("edit:build:{build_id}")
BUILD_LOG_CONSENT = sl.Route("build_log:consent")
REMOVE_REDSTONER_ROLE = sl.Route("remove:role:redstoner")


async def _route_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily: errors.py -> utils.components -> ui.py would otherwise cycle.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=source)


ROUTER = sl.discord.Router(on_error=_route_error_hook)

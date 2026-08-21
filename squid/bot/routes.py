"""The bot's stateless control routes, and the router that dispatches them.

These are the controls that must still work on a card posted by a past run of the bot:
the id encodes what the click refers to and the database owns the rest, so no session,
generation, or in-memory handler is involved. The routes live here rather than beside their
handlers so the card that draws a button and the code that answers it cannot drift.

Handlers register themselves with `router` from their own modules; `RedstoneSquid` installs
the router once every extension has loaded, which is what freezes the table.
"""

import discord

import squid_layouts as sl

poll_close = sl.Route("poll:close")
poll_refresh = sl.Route("poll:refresh")
build_edit = sl.Route("edit:build:{build_id}")
build_log_consent = sl.Route("build_log:consent")
remove_redstoner_role = sl.Route("remove:role:redstoner")


async def _route_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily: errors.py -> utils.components -> ui.py would otherwise cycle.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=source)


router = sl.discord.Router(on_error=_route_error_hook)

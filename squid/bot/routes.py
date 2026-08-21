"""The bot's stateless control routes, and the router that dispatches them.

These are the controls that must still work on a card posted by a past run of the bot:
the id encodes what the click refers to and the database owns the rest, so no session,
generation, or in-memory handler is involved. The routes live here rather than beside their
handlers so the card that draws a button and the code that answers it cannot drift.

Handlers register themselves with `router` from their own modules; `RedstoneSquid` installs
the router once every extension has loaded, which is what freezes the table.
"""

from typing import TYPE_CHECKING

import discord

import squid_layouts as sl

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

poll_close = sl.Route("r:polls:close", aliases=("poll:close",))
poll_refresh = sl.Route("r:polls:refresh", aliases=("poll:refresh",))
build_edit = sl.Route("r:builds:{build_id:int}:edit", aliases=("edit:build:{build_id:int}",))
build_log_consent = sl.Route("r:build-log-consents:new", aliases=("build_log:consent",))
remove_redstoner_role = sl.Route("r:redstoner-roles:self:remove", aliases=("remove:role:redstoner",))


async def _route_gone_hook(interaction: discord.Interaction) -> None:
    from squid.bot.i18n import resolve_locale, t
    from squid.bot.utils.components import reply_layout, text_layout
    from squid.core.i18n import _

    locale = await resolve_locale(interaction, interaction.client.services.settings)
    await reply_layout(interaction, text_layout(t(locale, _("This control is no longer available."))))


async def _route_error_hook(interaction: discord.Interaction, error: Exception, source: str) -> None:
    # Imported lazily: errors.py -> utils.components -> ui.py would otherwise cycle.
    from squid.bot.errors import handle_interaction_error

    await handle_interaction_error(interaction, error, surface=source)


# Annotated rather than subscripted at runtime, because `app` imports this module: PEP 649
# defers the annotation, so the client type is checked without the import cycle.
router: sl.discord.Router[RedstoneSquid] = sl.discord.Router(
    namespace="r",
    on_gone=_route_gone_hook,
    on_error=_route_error_hook,
)

"""Account-wide Discord build indexing controls."""

from typing import override

from discord import TextChannel

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest, AppRoutes
from squid.bot.ui import render_payload, text_node, tr
from squid.bot.utils.sticky_message import StickyMessage
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext, UserActor

router = AppRoutes()
indexing = router.group("/build-indexing")

_DESCRIPTION = tr(
    t"discord-indexing-description = Automatic indexing adds your builds from all participating Discord channels to the catalogue "
    t"and copies their attachments. Turning it off stops future indexing. Builds already in the catalogue stay there."
)


@indexing.route("/enable")
async def enable_indexing(request: AppRequest) -> None:
    context = ActingContext.for_actor(UserActor(discord_user_id=request.user.id))
    await request.client.services.accounts.set_discord_build_indexing(context=context, enabled=True)
    await request.respond(text_node(tr(t"discord-indexing-on = Automatic indexing is on.")), audience="personal")


@indexing.route("/disable")
async def disable_indexing(request: AppRequest) -> None:
    context = ActingContext.for_actor(UserActor(discord_user_id=request.user.id))
    await request.client.services.accounts.set_discord_build_indexing(context=context, enabled=False)
    await request.respond(text_node(tr(t"discord-indexing-off = Automatic indexing is off.")), audience="personal")


@indexing.route("/settings")
async def open_indexing_settings(request: AppRequest) -> None:
    account = await request.client.services.accounts.get_account_by_identity(
        IdentityProvider.DISCORD, str(request.user.id)
    )
    enabled = account is not None and account.discord_build_indexing_enabled
    await request.respond(
        sl.stack(
            sl.paragraph(_DESCRIPTION),
            sl.action_controls(
                sl.routed_action_control(
                    tr(t"discord-indexing-disable = Turn off automatic indexing")
                    if enabled
                    else tr(t"discord-indexing-enable = Turn on automatic indexing"),
                    (disable_indexing if enabled else enable_indexing).custom_id(),
                    key="indexing",
                ),
                key="actions",
            ),
        ),
        audience="personal",
    )


class BuildIndexingStickyMessage(StickyMessage):
    @override
    async def render(self, channel: TextChannel) -> sd.messages.payload.MessagePayload:
        return render_payload(
            [
                sl.section(
                    sl.heading(tr(t"discord-indexing-heading = Automatically index my Discord builds")),
                    sl.paragraph(_DESCRIPTION),
                ),
                sl.action_controls(
                    sl.routed_action_control(
                        tr(t"discord-indexing-manage = Manage automatic indexing"),
                        open_indexing_settings.custom_id(),
                        key="manage",
                        tone=sl.Tone.INFO,
                    ),
                    key="actions",
                ),
            ],
            composition=sl.planning.CompositionOptions(),
        )

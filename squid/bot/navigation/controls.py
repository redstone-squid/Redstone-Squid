"""Persistent destination buttons using the bot's screen router."""

from typing import TYPE_CHECKING

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.navigation.destinations import Destination
from squid.bot.navigation.router import InvalidAddress, ScreenRouter
from squid.bot.routes import routes
from squid.bot.ui import text_node
from squid.core.i18n import tr
from squid_i18n import TextLike

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid

navigation = routes.group("navigation")


def invalid_address_response() -> sd.Response:
    return sd.Response(
        text_node(
            tr(t"navigation-invalid-address = That screen address is invalid. Try /builds, /drafts, or /account.")
        ),
        audience="personal",
    )


@navigation.route("open:{address}")
async def open_address(interaction: discord.Interaction[RedstoneSquid], address: str) -> None:
    request = await sd.request(interaction)
    router = interaction.client.screen_router
    try:
        destination = router.parse(address)
    except InvalidAddress:
        await request.defer("private")
        await request.respond(invalid_address_response())
        return
    await request.defer(router.deferral(destination))
    await router.open(request, destination)


def destination_button(router: ScreenRouter, label: TextLike, destination: Destination) -> sl.primitives.RoutedButton:
    custom_id = open_address.id(address=router.format(destination))
    return sl.primitives.RoutedButton(label, custom_id)

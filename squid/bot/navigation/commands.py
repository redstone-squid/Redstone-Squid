"""Open a screen address under the invoking user's identity."""

from typing import TYPE_CHECKING, Self

import squid_ui.ext.discord as sd
from squid.bot.navigation.assembly import build_screen_router
from squid.bot.navigation.controls import invalid_address_response
from squid.bot.navigation.router import InvalidAddress
from squid.core.i18n import tr

if TYPE_CHECKING:
    from squid.bot.app import RedstoneSquid


class NavigationCommands(sd.Cog):
    @sd.command(
        name=tr(t"command-open-name = open"),
        description=tr(t"command-open-help = Open a screen address, such as /builds/123 or /account."),
        defer="private",
        options={
            "address": sd.Option(
                name=tr(t"command-open-address-name = address"),
                description=tr(t"command-open-address-help = A screen address, such as /builds/123."),
            )
        },
    )
    async def open_screen(self, request: sd.Request[Self, RedstoneSquid], address: str) -> sd.CommandResult:
        router = request.client.screen_router
        try:
            destination = router.parse(address)
        except InvalidAddress:
            return invalid_address_response()
        return await router.open(request, destination)


async def setup(bot: RedstoneSquid) -> None:
    router = build_screen_router()
    await bot.add_cog(NavigationCommands(bot))
    bot.screen_router = router

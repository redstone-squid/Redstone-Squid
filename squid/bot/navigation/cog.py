"""Open a screen address under the invoking user's identity."""

from typing import TYPE_CHECKING, Self

import squid_ui.ext.discord as sd
from squid.bot.ui import text_node
from squid.core.i18n import tr
from squid_ui.routing import InvalidAddress

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
        address = address.strip()
        if not address.startswith("/"):
            address = f"/{address}"
        router = request.client.ui_router
        try:
            match = router.resolve(address)
        except InvalidAddress:
            return sd.Response(
                text_node(
                    tr(
                        t"navigation-invalid-address = That screen address is invalid. Try /builds, /drafts, or /account."
                    )
                ),
                audience="personal",
            )
        return await router.dispatch(request, match)


async def setup(bot: RedstoneSquid) -> None:
    await bot.add_cog(NavigationCommands(bot))

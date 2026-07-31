"""A cog for verifying minecraft accounts."""

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.ui.views import ConfirmationView
from squid.bot.utils.components import no_mentions, text_layout
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="verify"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.user_service = bot.services.users

    @hybrid_group(name="account")
    async def account_group(self, ctx: Context[BotT]) -> None:
        """Link or unlink your Minecraft account."""
        await ctx.send_help("account")

    @account_group.command(name="link")
    @app_commands.describe(code=app_commands.locale_str(_("The code you received by running /link in the game.")))
    async def link(self, ctx: Context[BotT], code: str):
        """Link your minecraft account."""
        await self.user_service.link_minecraft_account(ctx.author.id, code)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(t(locale, _("Your Discord account has been linked with your Minecraft account."))),
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="unlink")
    async def unlink(self, ctx: Context[BotT]):
        """Unlink your minecraft account."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        view = ConfirmationView(t(locale, _("Are you sure you want to unlink your Minecraft account?")), locale=locale)
        await ctx.send(view=view, allowed_mentions=no_mentions())

        await view.wait()
        if view.value:
            if await self.user_service.unlink_minecraft_account(ctx.author.id):
                await ctx.send(
                    view=text_layout(
                        t(locale, _("Your Discord account has been unlinked from your Minecraft account."))
                    ),
                    allowed_mentions=no_mentions(),
                )
            else:
                await ctx.send(
                    view=text_layout(
                        t(
                            locale,
                            _(
                                "You don't have a Minecraft account linked to your Discord account, "
                                "or the unlinking failed."
                            ),
                        )
                    ),
                    allowed_mentions=no_mentions(),
                )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

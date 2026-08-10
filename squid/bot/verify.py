"""A cog for verifying minecraft accounts."""

from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.bot.consent import UserDataConsentView
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.ui.views import ConfirmationView
from squid.bot.utils.components import no_mentions, text_layout
from squid.bot.utils.permissions import check_is_global_admin
from squid.core.i18n import _

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="verify"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.account_service = bot.services.accounts

    @hybrid_group(name="account")
    async def account_group(self, ctx: Context[BotT]) -> None:
        """Link or unlink your Minecraft account."""
        await ctx.send_help("account")

    @account_group.command(name="link")
    @app_commands.describe(code=app_commands.locale_str(_("The code you received by running /link in the game.")))
    async def link(self, ctx: Context[BotT], code: str):
        """Link your minecraft account."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        consent_view = UserDataConsentView(ctx.author.id, locale=locale)
        message = await ctx.send(
            view=consent_view,
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )
        consent_view.bind_message(message)
        await consent_view.wait()
        if consent_view.consent is None:
            await ctx.send(
                view=text_layout(t(locale, _("Account linking cancelled. No user account information was stored."))),
                ephemeral=ctx.interaction is not None,
                allowed_mentions=no_mentions(),
            )
            return

        claimed = await self.account_service.link_minecraft_account(ctx.author.id, code, consent=consent_view.consent)
        message = t(locale, _("Your Discord account has been linked with your Minecraft account."))
        if claimed is not None:
            message += "\n" + t(
                locale,
                _("Build credits under **{name}** are now attributed to your account."),
                name=claimed.name,
            )
        await ctx.send(view=text_layout(message), allowed_mentions=no_mentions())

    @account_group.command(name="unlink")
    async def unlink(self, ctx: Context[BotT]):
        """Unlink your minecraft account."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        view = ConfirmationView(t(locale, _("Are you sure you want to unlink your Minecraft account?")), locale=locale)
        await ctx.send(view=view, allowed_mentions=no_mentions())

        await view.wait()
        if view.value:
            if await self.account_service.unlink_minecraft_account(ctx.author.id):
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

    @account_group.command(name="claim")
    @app_commands.describe(name=app_commands.locale_str(_("A creator name credited on builds you worked on.")))
    async def claim(self, ctx: Context[BotT], *, name: str) -> None:
        """Ask staff to credit you with an older creator name."""
        claim = await self.account_service.request_alias_claim(ctx.author.id, name)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(
                t(
                    locale,
                    _("Claim #{id} for **{name}** is awaiting staff approval."),
                    id=claim.id,
                    name=claim.alias_name,
                )
            ),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="claims")
    @check_is_global_admin()
    async def pending_claims(self, ctx: Context[BotT]) -> None:
        """List creator credit claims awaiting review."""
        claims = await self.account_service.pending_alias_claims()
        locale = await resolve_locale(ctx, self.bot.services.settings)
        body = "\n".join(f"**#{claim.id}** {claim.alias_name} (account {claim.account_id})" for claim in claims)
        await ctx.send(
            view=text_layout(body or t(locale, _("No creator credit claims are awaiting review."))),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="approve-claim")
    @check_is_global_admin()
    async def approve_claim(self, ctx: Context[BotT], claim_id: int) -> None:
        """Credit a claimant with the creator name they requested."""
        staff = await self.account_service.get_or_create_account(ctx.author.id)
        assert staff.id is not None
        claim = await self.account_service.approve_alias_claim(claim_id, staff_account_id=staff.id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(t(locale, _("Credited **{name}** to the claimant."), name=claim.alias_name)),
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="reject-claim")
    @check_is_global_admin()
    async def reject_claim(self, ctx: Context[BotT], claim_id: int) -> None:
        """Close a creator credit claim without crediting the claimant."""
        staff = await self.account_service.get_or_create_account(ctx.author.id)
        assert staff.id is not None
        claim = await self.account_service.reject_alias_claim(claim_id, staff_account_id=staff.id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(t(locale, _("Rejected the claim for **{name}**."), name=claim.alias_name)),
            allowed_mentions=no_mentions(),
        )


async def setup(bot: "squid.bot.app.RedstoneSquid"):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

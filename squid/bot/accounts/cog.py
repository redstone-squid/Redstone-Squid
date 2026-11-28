"""Open the account workspace and render public creator profiles."""

import re
from typing import TYPE_CHECKING, Self
from uuid import UUID

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.accounts.creator_page import CreatorPage
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.catalogue.browse import BuildBrowseScreen
from squid.bot.routes import ScreenUnavailable
from squid.bot.ui import text_node
from squid.bot.utils.autocomplete import suggests
from squid.core.accounts.domain import IdentityProvider
from squid.core.actors import ActingContext
from squid.core.i18n import tr
from squid.core.search.application.completion import render_value
from squid_i18n import md_tr
from squid_ui.content.text import Text
from squid_ui.ext.discord.i18n import to_locale_str

if TYPE_CHECKING:
    import squid.bot.app

DISCORD_USER = re.compile(r"(<@!?)?(?P<id>\d{17,20})(?(1)>)")
"""A pasted mention or raw user ID. Minecraft names are at most 16 characters, so they never match."""


class VerifyCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT], name="verify"):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.account_service = bot.services.accounts

    @sd.command(
        name=tr(t"command-account-name = account"),
        description=tr(t"command-account-help = Manage your account, credits, and notifications"),
    )
    async def account(self, request: sd.Request[Self, BotT]) -> sd.CommandResult:
        """Open your account workspace."""
        return await AccountWorkspace.open_account(request)

    @sd.command(
        name=tr(t"command-creator-name = creator"),
        description=tr(t"command-creator-help = Show a creator's public page"),
        options={
            "name": sd.Option(
                name=tr(t"command-creator-name-name = name"),
                description=tr(t"command-creator-name-help = A credited name, or a Discord mention or user ID"),
                autocomplete=suggests("creator_profiles"),
            ),
        },
    )
    async def creator(self, request: sd.Request[Self, BotT], name: str) -> sd.CommandResult:
        """Show a creator page in the channel.

        Accepts a suggestion's public ID, a credited name, or a pasted Discord mention or user ID.
        """
        name = name.strip()
        try:
            public_id = UUID(name)
        except ValueError:
            pass
        else:
            fallback = tr(t"creator-page-fallback-name = Creator")
            return await self._public_page(request, public_id, fallback, audience="public")
        if (match := DISCORD_USER.fullmatch(name)) is not None:
            user_id = int(match["id"])
            member = request.guild.get_member(user_id) if request.guild is not None else None
            label = member.display_name if member is not None else name
            return await self._discord_page(request, user_id, label, audience="public")
        alias = await self.account_service.get_creator_alias(name, context=ActingContext.current())
        if alias is None:
            return _personal(_missing_creator_page(name))
        query = f"creator:{render_value(alias.name, quoted=True)}"
        return await request.respond(
            await BuildBrowseScreen.create_search(request, query=query, typed=False), audience="public"
        )

    @sd.context_menu(
        name=to_locale_str(tr(t"command-creator-page-context = Creator Page")),
        type=discord.AppCommandType.user,
        defer="private",
    )
    async def creator_context_menu(
        self, request: sd.Request[Self, BotT], user: discord.Member | discord.User
    ) -> sd.CommandResult:
        """Show the right-clicked user's creator page privately, like any other lookup."""
        return await self._discord_page(request, user.id, user.display_name, audience="personal")

    async def _discord_page(
        self, request: sd.Request[Self, BotT], user_id: int, label: str, *, audience: sd.Audience
    ) -> sd.CommandResult:
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user_id))
        # ORM identity columns may still be None before flush.
        if account is None or account.public_account_id is None:  # pyright: ignore[reportUnnecessaryComparison]
            return _personal(_missing_creator_page(label))
        identity = account.identity_for(IdentityProvider.DISCORD, str(user_id))
        if identity is None or not identity.is_public:
            return _personal(_missing_creator_page(label))
        return await self._public_page(request, account.public_account_id, label, audience=audience)

    async def _public_page(
        self, request: sd.Request[Self, BotT], public_id: UUID, fallback_name: sl.TextLike, *, audience: sd.Audience
    ) -> sd.CommandResult:
        try:
            page = await CreatorPage.load(self.account_service, public_id, fallback_name)
        except ScreenUnavailable as error:
            return _personal(error.message)
        return await request.respond(page, audience=audience)


def _personal(message: sl.TextLike) -> sd.Response:
    """Lookup failures concern only the person who asked."""
    return sd.Response(text_node(message), audience="personal")


def _missing_creator_page(user: str) -> Text:
    return md_tr(t"bot-verify-doesn-t-have-a-creator-page = {user} doesn't have a creator page.")


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))
    bot.ui_router.mount(__name__, AccountWorkspace.open_account, CreatorPage.open)


async def teardown(bot: squid.bot.app.RedstoneSquid) -> None:
    bot.ui_router.unmount(__name__)

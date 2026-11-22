"""Open the account workspace and render public creator profiles."""

from typing import TYPE_CHECKING, Self
from uuid import UUID

import discord

import squid_ui.ext.discord as sd
from squid.bot.accounts.creator_page import CreatorPage
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.routes import ScreenUnavailable
from squid.bot.ui import text_node
from squid.bot.utils.autocomplete import suggests
from squid.core.accounts.domain import IdentityProvider
from squid.core.i18n import tr
from squid_i18n import md_tr
from squid_ui.content.text import Text

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT], name="verify"):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.account_service = bot.services.accounts

    @sd.command(
        name=tr(t"command-account-name = account"),
        description=tr(t"command-account-help = Manage your account or view a creator page"),
        options={
            "creator": sd.Option(
                name=tr(t"command-account-creator-name = creator"),
                description=tr(t"command-account-creator-help = Search public creator pages by credited name"),
                autocomplete=suggests("creator_profiles"),
            ),
            "user": sd.Option(
                name=tr(t"command-account-user-name = user"),
                description=tr(
                    t"verify-creator-page-target-prompt = Whose creator page to show. Defaults to your own account."
                ),
            ),
        },
    )
    async def account(
        self,
        request: sd.Request[Self, BotT],
        user: discord.Member | discord.User | None = None,
        creator: str | None = None,
    ) -> sd.CommandResult:
        """Show your account, or somebody else's creator page."""
        if creator is not None:
            try:
                public_id = UUID(creator)
            except ValueError:
                return text_node(tr(t"account-select-creator-result = Choose a creator from the name suggestions."))
            return await self._public_page(public_id, "Creator")
        actor = request.user
        if user is not None and user.id != actor.id:
            return await self._creator_page(request, user)

        return await AccountWorkspace.open_account(request)

    async def _creator_page(
        self, request: sd.Request[Self, BotT], user: discord.Member | discord.User
    ) -> sd.CommandResult:
        """Somebody else's page is shared content and answers where the channel sees it."""
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
        # ORM identity columns may still be None before flush.
        if account is None or account.public_creator_id is None:  # pyright: ignore[reportUnnecessaryComparison]
            return sd.Response(
                text_node(_missing_creator_page(user.display_name)),
                audience="personal",
            )
        return await self._public_page(account.public_creator_id, user.display_name)

    async def _public_page(self, public_id: UUID, fallback_name: str) -> sd.CommandResult:
        try:
            page = await CreatorPage.load(self.account_service, public_id, fallback_name)
        except ScreenUnavailable as error:
            return text_node(error.message)
        return sd.Response(page, audience="public")


def _missing_creator_page(user: str) -> Text:
    return md_tr(t"bot-verify-doesn-t-have-a-creator-page = {user} doesn't have a creator page.")


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))
    bot.ui_router.mount(__name__, AccountWorkspace.open_account, CreatorPage.open)


async def teardown(bot: squid.bot.app.RedstoneSquid) -> None:
    bot.ui_router.unmount(__name__)

"""Open the account workspace and render public creator profiles."""

from typing import TYPE_CHECKING, Self
from uuid import UUID

import discord

import squid_ui.ext.discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.accounts.profile_render import (
    public_profile_fields,
)
from squid.bot.accounts.workspace import AccountWorkspace
from squid.bot.follows import FollowCard
from squid.bot.ui import card_node, text_node
from squid.core.i18n import tr
from squid.notifications.domain import SubscriptionKind
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
            "user": sd.Option(
                name=tr(t"command-account-user-name = user"),
                description=tr(
                    t"verify-creator-page-target-prompt = Whose creator page to show. Defaults to your own account."
                ),
            )
        },
    )
    async def account(
        self, request: sd.Request[Self, BotT], user: discord.Member | discord.User | None = None
    ) -> sd.CommandResult:
        """Show your account, or somebody else's creator page."""
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
        return await self._public_profile_card(account.public_creator_id, user.display_name)

    async def _public_profile_card(self, public_id: UUID, fallback_name: str):
        """Render somebody else's page from the same filtered view the API serves."""
        public = await self.account_service.get_public_profile(public_id)
        if public is None:
            return text_node(
                tr(t"bot-verify-that-creator-page-could-not-be-found = That creator page could not be found.")
            )
        if public.hidden:
            return card_node(
                tr(t"bot-verify-hidden-creator-page = Hidden creator page"),
                tr(
                    t"verify-creator-page-hidden = This creator has hidden their "
                    t"page. Their build credit is still listed."
                ),
                fields=public_profile_fields(public),
            )
        return FollowCard(
            card_node(
                public.display_name or fallback_name,
                public.bio,
                fields=public_profile_fields(public),
                media=() if public.avatar_url is None else (public.avatar_url,),
            ),
            SubscriptionKind.CREATOR,
            public_id,
        )


def _missing_creator_page(user: str) -> Text:
    return md_tr(t"bot-verify-doesn-t-have-a-creator-page = {user} doesn't have a creator page.")


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

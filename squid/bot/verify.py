"""A cog for verifying minecraft accounts."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Self
from uuid import UUID

import discord
from discord import app_commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import Account, AccountConsent, IdentityProvider, LinkPreview
from squid.bot.account_view import ConsentAnswer
from squid.bot.account_workspace import AccountWorkspace
from squid.bot.consent import request_consent
from squid.bot.profile_render import (
    public_profile_fields,
)
from squid.bot.ui import card_node, text_node
from squid.bot.utils.permissions import allows
from squid.core.i18n import tr
from squid.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_IDENTITY_REFRESH,
    ACCOUNT_IDENTITY_REFRESH_ANY,
)

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: "squid.bot.app.RedstoneSquid"](sd.Cog[BotT], name="verify"):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.account_service = bot.services.accounts

    @sd.command(name="account", description="Manage your account or view a creator page")
    @app_commands.describe(user=app_commands.locale_str("Whose creator page to show. Defaults to your own account."))
    async def account(
        self, request: sd.Request[Self], user: discord.Member | discord.User | None = None
    ) -> sd.CommandResult:
        """Show your account, or somebody else's creator page."""
        actor = request.user
        if user is not None and user.id != actor.id:
            return await self._creator_page(request, user)

        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(actor.id))
        if account is not None:
            await self._refresh_discord_avatar_key(account, actor)

        async def open_consent(
            event: sl.ActionEvent,
            answered: ConsentAnswer,
            *,
            preview: LinkPreview | None = None,
            on_abandon: Callable[[], Awaitable[None]] | None = None,
            timeout: float = 120.0,
        ) -> bool:
            press = await sd.request(event)
            message_root = press.root
            assert message_root is not None, "a press always arrives from a mounted message"

            async def completed(prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
                await answered(prompt, consent)
                if consent is not None:
                    await message_root.schedule()

            return await request_consent(
                press,
                user_id=actor.id,
                on_answer=completed,
                preview=preview,
                on_abandon=on_abandon,
                timeout=timeout,
                parent=message_root,
            )

        async def authorize_claim(node) -> bool:
            return await allows(request, node)

        return AccountWorkspace(
            accounts=self.account_service,
            actor_id=actor.id,
            account=account,
            request_consent=open_consent,
            can_review_claims=await allows(request, ACCOUNT_CLAIM_LIST),
            can_approve_claims=await allows(request, ACCOUNT_CLAIM_APPROVE),
            can_reject_claims=await allows(request, ACCOUNT_CLAIM_REJECT),
            authorize_claim=authorize_claim,
            can_refresh_any=await allows(request, ACCOUNT_IDENTITY_REFRESH_ANY),
            can_refresh_identity=await allows(request, ACCOUNT_IDENTITY_REFRESH),
        )

    async def _creator_page(self, request: sd.Request[Self], user: discord.Member | discord.User) -> sd.CommandResult:
        """Somebody else's page is shared content and answers where the channel sees it."""
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
        if account is None or account.public_creator_id is None:
            return sd.Response(
                text_node(_missing_creator_page(user.display_name)),
                audience="personal",
            )
        return await self._public_profile_card(account.public_creator_id, user.display_name)

    async def _public_profile_card(self, public_id: UUID, fallback_name: str):
        """Render somebody else's page from the same filtered view the API serves."""
        public = await self.account_service.get_public_profile(public_id)
        if public is None:
            return text_node(tr(tr(t"That creator page could not be found.")))
        if public.hidden:
            return card_node(
                tr(tr(t"Hidden creator page")),
                tr(tr(t"This creator has hidden their page. Their build credit is still listed.")),
                fields=public_profile_fields(public),
            )
        return card_node(
            public.display_name or fallback_name,
            public.bio,
            fields=public_profile_fields(public),
            media=() if public.avatar_url is None else (public.avatar_url,),
        )

    async def _refresh_discord_avatar_key(self, account: Account, user: discord.Member | discord.User) -> None:
        """Store the viewer's current Discord avatar hash, which only the gateway supplies.

        Opportunistic: this is the one place the bot reliably holds both the account and a fresh
        `discord.User`, so an avatar that would otherwise render as null gets filled in whenever
        someone looks at their own page.
        """
        identity = account.identity_for(IdentityProvider.DISCORD, str(user.id))
        if account.id is None or identity is None or identity.id is None:
            return
        key = user.avatar.key if user.avatar is not None else None
        if key != identity.avatar_key:
            await self.account_service.record_identity_avatar_key(account.id, identity.id, key)


def _missing_creator_page(user: str) -> str:
    return tr(tr(t"{user} doesn't have a creator page."))


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

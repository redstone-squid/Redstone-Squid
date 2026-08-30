"""A cog for verifying minecraft accounts."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING
from uuid import UUID

import discord
from discord import app_commands

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import (
    Account,
    AccountConsent,
    AccountIdentity,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
)
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
)
from squid_ui_discord.ext import Cog

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: "squid.bot.app.RedstoneSquid"](Cog[BotT], name="verify"):
    def __init__(self, bot: BotT):
        super().__init__(bot)
        self.account_service = bot.services.accounts

    @app_commands.command(name="account", description="Manage your account or view a creator page")
    @app_commands.describe(user=app_commands.locale_str("Whose creator page to show. Defaults to your own account."))
    async def account(
        self, interaction: discord.Interaction[BotT], user: discord.Member | discord.User | None = None
    ) -> None:
        """Show your account, or somebody else's creator page."""
        if user is not None and user.id != interaction.user.id:
            await self._show_creator_page(interaction, user)
            return

        account = await self.account_service.get_account_by_identity(
            IdentityProvider.DISCORD,
            str(interaction.user.id),
        )
        if account is not None:
            await self._refresh_discord_avatar_key(account, interaction.user)

        async def open_consent(
            event: sl.ActionEvent,
            answered: Callable[[AccountConsent | None], Awaitable[None]],
        ) -> None:
            message_root = sd.responder(event).message_root

            async def completed(_prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
                await answered(consent)
                if consent is not None:
                    await message_root.schedule()

            await request_consent(
                sd.native(event),
                user_id=interaction.user.id,
                on_answer=completed,
                parent=message_root,
            )

        async def authorize_claim(node) -> bool:
            return await allows(interaction, node)

        await self.ui.respond(
            interaction,
            AccountWorkspace(
                accounts=self.account_service,
                actor_id=interaction.user.id,
                account=account,
                request_consent=open_consent,
                can_review_claims=await allows(interaction, ACCOUNT_CLAIM_LIST),
                can_approve_claims=await allows(interaction, ACCOUNT_CLAIM_APPROVE),
                can_reject_claims=await allows(interaction, ACCOUNT_CLAIM_REJECT),
                authorize_claim=authorize_claim,
            ),
        )

    async def _show_creator_page(
        self,
        interaction: discord.Interaction[BotT],
        user: discord.Member | discord.User,
    ) -> None:
        """Show somebody else's page, which is shared content and answers where the channel sees it."""
        invocation = await sd.Invocation.of(interaction)
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
        if account is None or account.public_creator_id is None:
            await invocation.reply(
                text_node(tr("{user} doesn't have a creator page.", user=user.display_name)),
                visibility="personal",
            )
            return
        node = await self._public_profile_card(account.public_creator_id, user.display_name)
        await invocation.reply(node)

    async def _public_profile_card(self, public_id: UUID, fallback_name: str):
        """Render somebody else's page from the same filtered view the API serves."""
        public = await self.account_service.get_public_profile(public_id)
        if public is None:
            return text_node(tr("That creator page could not be found."))
        if public.hidden:
            return card_node(
                tr("Hidden creator page"),
                tr("This creator has hidden their page. Their build credit is still listed."),
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
        identity = next(
            (
                candidate
                for candidate in account.identities
                if candidate.provider is IdentityProvider.DISCORD and candidate.discord_id == user.id
            ),
            None,
        )
        if account.id is None or identity is None or identity.id is None:
            return
        key = user.avatar.key if user.avatar is not None else None
        if key != identity.avatar_key:
            await self.account_service.record_identity_avatar_key(account.id, identity.id, key)


def _link_conflict(preview: LinkPreview, existing_java: AccountIdentity | None) -> UUID | None:
    """Return the Minecraft UUID that makes this link impossible, or `None` if it can proceed.

    Both cases used to surface only after the notice had been read and agreed to, because they are
    checked inside the redemption. The reservation makes them answerable first, which is the
    difference between "that cannot work" and "you consented to something that then failed".

    Relinking the *same* UUID is not a conflict: it is how a renamed player refreshes their name.
    """
    if existing_java is not None and existing_java.java_uuid != preview.java_uuid:
        return existing_java.java_uuid
    if preview.java_uuid_held_elsewhere and (existing_java is None or existing_java.java_uuid != preview.java_uuid):
        return preview.java_uuid
    return None


def _link_message(refresh: IdentityRefresh) -> str:
    """Render the outcome of a link in the same words a refresh uses.

    Linking used to report only the alias it claimed, which cannot express the contested case at all:
    a user whose verified name belonged to somebody else was told the link succeeded and never that
    their credit had not moved. The reconciliation is the same operation in both commands, so it gets
    the same vocabulary; only the headline differs.
    """
    lines = [
        tr(
            "Your Discord account is now linked to **{name}**.",
            name=refresh.current_name,
        )
    ]
    lines.extend(_reconciliation_lines(refresh))
    return "\n".join(lines)


def _refresh_message(refresh: IdentityRefresh) -> str:
    """Render every branch of a refresh, including the one where nothing changed."""
    if not refresh.renamed:
        lines = [tr("Your Minecraft name is still **{name}**. Nothing changed.", name=refresh.current_name)]
    else:
        lines = [
            tr(
                "Your Minecraft name changed from **{old}** to **{new}**.",
                old=refresh.previous_name,
                new=refresh.current_name,
            )
        ]
    lines.extend(_reconciliation_lines(refresh))
    return "\n".join(lines)


def _reconciliation_lines(refresh: IdentityRefresh) -> list[str]:
    """Describe what happened to the creator credit, shared by linking and refreshing."""
    lines: list[str] = []
    if refresh.claimed_alias is not None:
        lines.append(
            tr(
                "Build credits under **{name}** are attributed to your account.",
                name=refresh.claimed_alias.name,
            )
        )
    elif refresh.contested_alias is not None:
        lines.append(
            tr(
                "**{name}** is already credited to another account, so it was not moved. "
                "Claim #{id} is awaiting staff review.",
                name=refresh.contested_alias.name,
                id=refresh.opened_claim.id if refresh.opened_claim is not None else 0,
            )
        )

    if refresh.retained_alias_names:
        lines.append(
            tr(
                "You are still credited under: {names}.",
                names=", ".join(f"**{name}**" for name in refresh.retained_alias_names),
            )
        )
    return lines


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

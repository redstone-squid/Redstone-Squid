"""The `/account` command: the caller's account workspace, or another user's public creator page."""

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Self
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
        """Own account opens the personal `AccountWorkspace`; another user's page is a shared card."""
        actor = request.user
        if user is not None and user.id != actor.id:
            return await self._creator_page(request, user)

        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(actor.id))
        if account is not None:
            await self._refresh_discord_avatar_key(account, actor)

        async def open_consent(
            event: sl.ActionEvent,
            answered: Callable[[AccountConsent | None], Awaitable[None]],
        ) -> None:
            press = await sd.request(event)
            message_root = press.root
            assert message_root is not None, "a press always arrives from a mounted message"

            async def completed(_prompt: sl.PressEvent, consent: AccountConsent | None) -> None:
                await answered(consent)
                if consent is not None:
                    await message_root.schedule()

            await request_consent(press, user_id=actor.id, on_answer=completed, parent=message_root)

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
        )

    async def _creator_page(self, request: sd.Request[Self], user: discord.Member | discord.User) -> sd.CommandResult:
        """Shared card in the channel; only the "no creator page" refusal is personal."""
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
        if account is None or account.public_creator_id is None:
            return sd.Response(
                text_node(tr("{user} doesn't have a creator page.", user=user.display_name)), audience="personal"
            )
        return await self._public_profile_card(account.public_creator_id, user.display_name)

    async def _public_profile_card(self, public_id: UUID, fallback_name: str):
        """Renders the same `PublicCreatorProfile` the API serves; a hidden page still lists build credit."""
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
        """Store the viewer's current Discord avatar hash if it changed; only the gateway supplies it."""
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
    """The Minecraft UUID that blocks this link, or None if it can proceed.

    Relinking the same UUID is not a conflict; that is how a renamed player refreshes their name.
    """
    if existing_java is not None and existing_java.java_uuid != preview.java_uuid:
        return existing_java.java_uuid
    if preview.java_uuid_held_elsewhere and (existing_java is None or existing_java.java_uuid != preview.java_uuid):
        return preview.java_uuid
    return None


def _link_message(refresh: IdentityRefresh) -> str:
    """Link headline plus the shared `_reconciliation_lines`, so a contested alias is reported, not just a claimed one."""
    lines = [
        tr(
            "Your Discord account is now linked to **{name}**.",
            name=refresh.current_name,
        )
    ]
    lines.extend(_reconciliation_lines(refresh))
    return "\n".join(lines)


def _refresh_message(refresh: IdentityRefresh) -> str:
    """Refresh headline (including the unchanged case) plus the shared `_reconciliation_lines`."""
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
    """Claimed or contested alias, then retained names; empty when credit is untouched."""
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
    await bot.add_cog(VerifyCog(bot))

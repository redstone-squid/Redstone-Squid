"""A cog for verifying minecraft accounts."""

from typing import TYPE_CHECKING
from uuid import UUID

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.accounts.domain import AccountIdentity, AliasClaim, IdentityProvider, IdentityRefresh, LinkPreview
from squid.accounts.errors import AccountAlreadyLinkedError, AccountNotFoundError
from squid.bot.consent import UserDataConsentView
from squid.bot.i18n import resolve_locale, t
from squid.bot.submission.ui.views import ConfirmationView
from squid.bot.utils.accounts import account_id_for
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.components import DISCORD_BLUE, CardField, card_layout, no_mentions, text_layout
from squid.bot.utils.permissions import PermissionNodeRequired, requires, subject_for
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_IDENTITY_REFRESH_ANY,
)

if TYPE_CHECKING:
    import squid.bot.app

CLAIM_QUEUE_PAGE = 10
"""Claims listed at once.

Bounded so the card never has to be truncated mid-entry; the footer reports the rest.
"""


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
        ephemeral = ctx.interaction is not None
        attempted_by = (IdentityProvider.DISCORD, str(ctx.author.id))

        # Read without creating: nobody gets an account row for typing a code that turns out to be
        # wrong, and the conflict checks below need whatever they already have.
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(ctx.author.id))
        existing_java = None if account is None else account.identity(IdentityProvider.JAVA)

        # Hold the code before prompting, so a wrong code fails here rather than after the notice has
        # been read and agreed to, and so the prompt can say what it is actually asking about.
        reservation = await self.account_service.reserve_minecraft_link(code, attempted_by=attempted_by)
        committed = False
        try:
            conflict = _link_conflict(reservation.preview, existing_java)
            if conflict is not None:
                raise AccountAlreadyLinkedError(
                    minecraft_uuid=conflict,
                    account_id=None if account is None else account.id,
                    provider=IdentityProvider.DISCORD,
                    subject=str(ctx.author.id),
                )

            consent_view = UserDataConsentView(ctx.author.id, reservation.preview, locale=locale)
            message = await ctx.send(view=consent_view, ephemeral=ephemeral, allowed_mentions=no_mentions())
            consent_view.bind_message(message)
            await consent_view.wait()
            if consent_view.consent is None:
                await ctx.send(
                    view=text_layout(
                        t(locale, _("Account linking cancelled. No user account information was stored."))
                    ),
                    ephemeral=ephemeral,
                    allowed_mentions=no_mentions(),
                )
                return

            # The account is created here rather than by the redemption, which is evidence of a
            # Java subject and of nothing else. This command is reached over the gateway, so it
            # genuinely holds the Discord identity it is about to mint.
            account_id = await account_id_for(self.account_service, ctx.author)
            refresh = await self.account_service.link_minecraft_account(
                account_id,
                code,
                consent=consent_view.consent,
                attempted_by=attempted_by,
                reservation=reservation,
            )
            committed = True
        finally:
            # Every exit that did not redeem gives the code back, so a conflict, a cancellation or a
            # timeout can be retried at once instead of waiting out the hold.
            if not committed:
                await self.account_service.release_minecraft_link(code, reservation)

        await ctx.send(
            view=text_layout(_link_message(refresh, locale)),
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="unlink")
    async def unlink(self, ctx: Context[BotT]):
        """Unlink your minecraft account."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        ephemeral = ctx.interaction is not None
        view = ConfirmationView(t(locale, _("Are you sure you want to unlink your Minecraft account?")), locale=locale)
        await ctx.send(view=view, ephemeral=ephemeral, allowed_mentions=no_mentions())

        await view.wait()
        await ctx.send(
            view=text_layout(await self._unlink_outcome(ctx, view.value, locale)),
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
        )

    async def _unlink_outcome(self, ctx: Context[BotT], confirmed: bool | None, locale: str) -> str:
        """Say what happened for all three answers, including no answer at all.

        A timed-out confirmation used to send nothing, leaving a dead prompt and no way to tell
        "nothing happened" from "something went wrong".
        """
        if confirmed is None:
            return t(locale, _("The confirmation expired, so nothing was unlinked."))
        if not confirmed:
            return t(locale, _("Cancelled. Your Minecraft account is still linked."))

        # Read rather than get-or-create: someone with no account has nothing to unlink,
        # and unlinking is no reason to write a row for them.
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(ctx.author.id))
        if account is None or account.id is None or account.identity(IdentityProvider.JAVA) is None:
            # Split from the failure below, which used to share one message joined by "or" and so
            # told the user neither of the two things it might mean.
            return t(locale, _("You don't have a Minecraft account linked, so there was nothing to unlink."))
        if not await self.account_service.unlink_minecraft_account(account.id):
            return t(locale, _("Unlinking failed. Please try again, and report it if it keeps happening."))
        return t(locale, _("Your Discord account has been unlinked from your Minecraft account."))

    @account_group.command(name="refresh")
    @app_commands.describe(
        user=app_commands.locale_str(_("Whose linked account to refresh. Staff only; defaults to you."))
    )
    async def refresh(self, ctx: Context[BotT], user: discord.Member | discord.User | None = None) -> None:
        """Re-read your Minecraft name after a rename and update your creator credit."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        target = user or ctx.author
        if target.id != ctx.author.id:
            # Checked here rather than by `@requires`, because refreshing your own account is
            # allowed by default and only the staff form needs the moderation node.
            subject = await subject_for(ctx)
            if not await self.bot.services.permissions.allows(subject, ACCOUNT_IDENTITY_REFRESH_ANY):
                raise PermissionNodeRequired((ACCOUNT_IDENTITY_REFRESH_ANY.name,))
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(target.id))
        if account is None or account.id is None:
            raise AccountNotFoundError(provider=IdentityProvider.DISCORD, subject=str(target.id))

        refresh = await self.account_service.refresh_java_identity(account.id)
        await ctx.send(
            view=text_layout(_refresh_message(refresh, locale)),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @autocompletes(name="creators")
    @account_group.command(name="claim")
    @app_commands.describe(name=app_commands.locale_str(_("A creator name credited on builds you worked on.")))
    async def claim(self, ctx: Context[BotT], *, name: str) -> None:
        """Ask staff to credit you with an older creator name."""
        account_id = await account_id_for(self.account_service, ctx.author)
        claim = await self.account_service.request_alias_claim(account_id, name)
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
    @requires(ACCOUNT_CLAIM_LIST)
    async def pending_claims(self, ctx: Context[BotT]) -> None:
        """List creator credit claims awaiting review."""
        claims = await self.account_service.pending_alias_claims(with_claimants=True)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        if not claims:
            await ctx.send(
                view=text_layout(t(locale, _("No creator credit claims are awaiting review."))),
                ephemeral=ctx.interaction is not None,
                allowed_mentions=no_mentions(),
            )
            return

        shown = claims[:CLAIM_QUEUE_PAGE]
        footer = None
        if len(claims) > len(shown):
            # Said explicitly rather than letting `card_container` truncate: a review queue that
            # quietly stops listing work is worse than one that admits it is longer.
            footer = t(
                locale,
                _("{remaining} more not shown."),
                remaining=len(claims) - len(shown),
            )
        await ctx.send(
            view=card_layout(
                t(locale, _("Creator credit claims awaiting review")),
                accent_colour=DISCORD_BLUE,
                fields=[
                    CardField(
                        t(locale, _("Claim #{id} — {name}"), id=claim.id, name=claim.alias_name),
                        t(
                            locale,
                            _("{claimant} · opened {age}"),
                            claimant=present_claimant(claim, locale),
                            age=discord.utils.format_dt(claim.created_at.to_stdlib(), style="R"),
                        ),
                    )
                    for claim in shown
                ],
                footer=footer,
            ),
            ephemeral=ctx.interaction is not None,
            allowed_mentions=no_mentions(),
        )

    @autocompletes(claim_id="alias_claims_pending")
    @account_group.command(name="approve-claim")
    @app_commands.describe(
        reassign=app_commands.locale_str(
            _("Take the name from the account currently credited with it. Required for a contested claim.")
        )
    )
    @requires(ACCOUNT_CLAIM_APPROVE)
    async def approve_claim(self, ctx: Context[BotT], claim_id: int, reassign: bool = False) -> None:
        """Credit a claimant with the creator name they requested."""
        staff_account_id = await account_id_for(self.account_service, ctx.author)
        claim = await self.account_service.approve_alias_claim(
            claim_id, staff_account_id=staff_account_id, reassign=reassign
        )
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(
                t(
                    locale,
                    _("Credited **{name}** to {claimant}."),
                    name=claim.alias_name,
                    claimant=present_claimant(claim, locale),
                )
            ),
            allowed_mentions=no_mentions(),
        )

    @autocompletes(claim_id="alias_claims_pending")
    @account_group.command(name="reject-claim")
    @requires(ACCOUNT_CLAIM_REJECT)
    async def reject_claim(self, ctx: Context[BotT], claim_id: int) -> None:
        """Reject a creator credit claim, leaving the name credited as it is."""
        staff_account_id = await account_id_for(self.account_service, ctx.author)
        claim = await self.account_service.reject_alias_claim(claim_id, staff_account_id=staff_account_id)
        locale = await resolve_locale(ctx, self.bot.services.settings)
        await ctx.send(
            view=text_layout(
                t(
                    locale,
                    _("Closed {claimant}'s claim on **{name}** without crediting it."),
                    name=claim.alias_name,
                    claimant=present_claimant(claim, locale),
                )
            ),
            allowed_mentions=no_mentions(),
        )


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


def present_claimant(claim: AliasClaim, locale: str | None = None) -> str:
    """Name a claimant by the most recognisable identity loaded for them.

    One function for all four surfaces that show a claimant — the queue, an approval, a rejection and
    the claim-id autocomplete — so a reviewer reads the same thing everywhere.

    A Discord mention is preferred because it is the only handle a reviewer can click, and because a
    Discord identity never gets a stored `display_name`; Discord resolves the snowflake client-side.
    The internal account ID is last and is labelled as a diagnostic, since it identifies a row rather
    than a person.
    """
    claimant = claim.claimant
    if claimant is not None:
        discord = claimant.identity(IdentityProvider.DISCORD)
        if discord is not None and discord.discord_id is not None:
            return f"<@{discord.discord_id}>"
        java = claimant.identity(IdentityProvider.JAVA)
        if java is not None and java.display_name is not None:
            return java.display_name
        if claimant.public_creator_id is not None:
            return t(locale, _("creator `{creator_id}`"), creator_id=claimant.public_creator_id)
    return t(locale, _("unidentified account (internal ID `{account_id}`)"), account_id=claim.account_id)


def _link_message(refresh: IdentityRefresh, locale: str) -> str:
    """Render the outcome of a link in the same words a refresh uses.

    Linking used to report only the alias it claimed, which cannot express the contested case at all:
    a user whose verified name belonged to somebody else was told the link succeeded and never that
    their credit had not moved. The reconciliation is the same operation in both commands, so it gets
    the same vocabulary; only the headline differs.
    """
    lines = [
        t(
            locale,
            _("Your Discord account is now linked to **{name}**."),
            name=refresh.current_name,
        )
    ]
    lines.extend(_reconciliation_lines(refresh, locale))
    return "\n".join(lines)


def _refresh_message(refresh: IdentityRefresh, locale: str) -> str:
    """Render every branch of a refresh, including the one where nothing changed."""
    if not refresh.renamed:
        lines = [t(locale, _("Your Minecraft name is still **{name}**. Nothing changed."), name=refresh.current_name)]
    else:
        lines = [
            t(
                locale,
                _("Your Minecraft name changed from **{old}** to **{new}**."),
                old=refresh.previous_name,
                new=refresh.current_name,
            )
        ]
    lines.extend(_reconciliation_lines(refresh, locale))
    return "\n".join(lines)


def _reconciliation_lines(refresh: IdentityRefresh, locale: str) -> list[str]:
    """Describe what happened to the creator credit, shared by linking and refreshing."""
    lines: list[str] = []
    if refresh.claimed_alias is not None:
        lines.append(
            t(
                locale,
                _("Build credits under **{name}** are attributed to your account."),
                name=refresh.claimed_alias.name,
            )
        )
    elif refresh.contested_alias is not None:
        lines.append(
            t(
                locale,
                _(
                    "**{name}** is already credited to another account, so it was not moved. "
                    "Claim #{id} is awaiting staff review."
                ),
                name=refresh.contested_alias.name,
                id=refresh.opened_claim.id if refresh.opened_claim is not None else 0,
            )
        )

    if refresh.retained_alias_names:
        lines.append(
            t(
                locale,
                _("You are still credited under: {names}."),
                names=", ".join(f"**{name}**" for name in refresh.retained_alias_names),
            )
        )
    return lines


async def setup(bot: squid.bot.app.RedstoneSquid):
    """Called by discord.py when the cog is added to the bot via bot.load_extension."""
    await bot.add_cog(VerifyCog(bot))

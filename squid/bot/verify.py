"""A cog for verifying minecraft accounts."""

from typing import TYPE_CHECKING
from uuid import UUID

import discord
from discord import app_commands
from discord.ext.commands import Cog, Context, hybrid_group

from squid.accounts.domain import (
    CURRENT_CONSENT_VERSION,
    Account,
    AccountIdentity,
    IdentityProvider,
    IdentityRefresh,
    LinkPreview,
)
from squid.accounts.errors import AccountAlreadyLinkedError, AccountNotFoundError
from squid.bot.account_view import AccountPanel
from squid.bot.claims_view import ClaimReviewComponent
from squid.bot.consent import ensure_consented_account, prompt_for_consent
from squid.bot.i18n import resolve_locale, t
from squid.bot.profile_render import (
    public_profile_fields,
)
from squid.bot.submission.ui.views import ConfirmationView
from squid.bot.utils.autocomplete import autocompletes
from squid.bot.utils.components import DISCORD_BLUE, card_layout, no_mentions, text_layout
from squid.bot.utils.permissions import PermissionNodeRequired, requires, subject_for
from squid.bot.utils.visibility import deliver_privately, personal
from squid.core.i18n import _
from squid.permissions.domain.catalogue import (
    ACCOUNT_CLAIM_APPROVE,
    ACCOUNT_CLAIM_LIST,
    ACCOUNT_CLAIM_REJECT,
    ACCOUNT_IDENTITY_REFRESH_ANY,
)

if TYPE_CHECKING:
    import squid.bot.app


class VerifyCog[BotT: "squid.bot.app.RedstoneSquid"](Cog, name="verify"):
    def __init__(self, bot: BotT):
        self.bot = bot
        self.account_service = bot.services.accounts

    @hybrid_group(name="account", fallback="show")
    @app_commands.describe(user=app_commands.locale_str(_("Whose creator page to show. Defaults to your own account.")))
    async def account_group(self, ctx: Context[BotT], user: discord.Member | discord.User | None = None) -> None:
        """Show your account, or somebody else's creator page."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        if user is not None and user.id != ctx.author.id:
            await self._show_creator_page(ctx, user, locale)
            return

        # Read without creating: looking at your own account is not evidence that anybody asked
        # to be remembered, and there is nothing to show for someone with no account anyway.
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(ctx.author.id))
        if account is None or account.id is None:
            await ctx.send(
                view=text_layout(
                    t(locale, _("You don't have any linked accounts yet. Link one with `/account link`."))
                ),
                ephemeral=personal(ctx),
                allowed_mentions=no_mentions(),
            )
            return

        await self._refresh_discord_avatar_key(account, ctx.author)
        component = AccountPanel(
            accounts=self.account_service,
            account_id=account.id,
            author_id=ctx.author.id,
            locale=locale,
        )
        await component.load()
        mount = component.mount()
        rendered = mount.build_view()
        message = await ctx.send(
            view=rendered,
            files=mount.attachment_files(),
            ephemeral=personal(ctx),
            allowed_mentions=no_mentions(),
        )
        mount.bind(message, rendered)

    async def _show_creator_page(self, ctx: Context[BotT], user: discord.Member | discord.User, locale: str) -> None:
        """Show somebody else's page, which is shared content and answers where the channel sees it."""
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(user.id))
        if account is None or account.public_creator_id is None:
            await ctx.send(
                view=text_layout(t(locale, _("{user} doesn't have a creator page."), user=user.display_name)),
                ephemeral=personal(ctx),
                allowed_mentions=no_mentions(),
            )
            return
        view = await self._public_profile_card(account.public_creator_id, user.display_name, locale)
        await ctx.send(view=view, allowed_mentions=no_mentions())

    @account_group.command(name="link")
    @app_commands.describe(code=app_commands.locale_str(_("The code you received by running /link in the game.")))
    async def link(self, ctx: Context[BotT], code: str):
        """Link your minecraft account."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        ephemeral = personal(ctx)
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

            consent = await prompt_for_consent(ctx, user_id=ctx.author.id, locale=locale, preview=reservation.preview)
            if consent is None:
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
            # genuinely holds the Discord identity it is about to mint, and it carries the receipt
            # into the same write so a row never exists without one.
            account = await self.account_service.get_or_create_identity(
                IdentityProvider.DISCORD, str(ctx.author.id), consent=consent
            )
            assert account.id is not None, "get_or_create_identity always returns a persisted account"
            account_id = account.id
            refresh = await self.account_service.link_minecraft_account(
                account_id,
                code,
                consent=consent,
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

    @account_group.command(name="consent")
    async def consent(self, ctx: Context[BotT]) -> None:
        """Read the privacy notice and accept it."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        ephemeral = personal(ctx)
        account = await self.account_service.get_account_by_identity(IdentityProvider.DISCORD, str(ctx.author.id))
        if account is not None and account.id is not None and not account.needs_consent_refresh:
            await ctx.send(
                view=text_layout(
                    t(
                        locale,
                        _("You have already accepted notice `{version}`. Press the button to read it again."),
                        version=CURRENT_CONSENT_VERSION,
                    )
                ),
                ephemeral=ephemeral,
                allowed_mentions=no_mentions(),
            )
            return

        account_id = await ensure_consented_account(ctx, self.account_service, locale=locale)
        if account_id is None:
            return
        await ctx.send(
            view=text_layout(t(locale, _("Thanks. You can use the bot's other commands now."))),
            ephemeral=ephemeral,
            allowed_mentions=no_mentions(),
        )

    async def _public_profile_card(self, public_id: UUID, fallback_name: str, locale: str):
        """Render somebody else's page from the same filtered view the API serves."""
        public = await self.account_service.get_public_profile(public_id)
        if public is None:
            return text_layout(t(locale, _("That creator page could not be found.")))
        if public.hidden:
            return card_layout(
                t(locale, _("Hidden creator page")),
                t(locale, _("This creator has hidden their page. Their build credit is still listed.")),
                accent_colour=DISCORD_BLUE,
                fields=public_profile_fields(public, locale),
            )
        return card_layout(
            public.display_name or fallback_name,
            public.bio,
            accent_colour=DISCORD_BLUE,
            fields=public_profile_fields(public, locale),
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
            ephemeral=personal(ctx),
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="merge-code")
    async def merge_code(self, ctx: Context[BotT]) -> None:
        """Offer this account up to be absorbed by another account you hold."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        account_id = await ensure_consented_account(ctx, self.account_service, locale=locale)
        if account_id is None:
            return
        code, ticket = await self.account_service.create_merge_code(account_id)
        # The comment here used to say "always ephemeral, even from a prefix invocation", which
        # `Context.send` cannot honour: it drops the flag without an interaction, so `!account
        # merge-code` posted an account-takeover credential into the channel.
        await deliver_privately(
            ctx,
            card_layout(
                t(locale, _("Merge code")),
                t(
                    locale,
                    _(
                        "Run `/account merge {code}` while signed in as the account you want to **keep**. "
                        "This account is the one that will be absorbed."
                    ),
                    code=code,
                ),
                accent_colour=DISCORD_BLUE,
                footer=t(
                    locale,
                    _("Expires {expiry}. Give it to nobody but yourself: it hands this account over."),
                    expiry=discord.utils.format_dt(ticket.expires_at.to_stdlib(), style="R"),
                ),
            ),
            reason=t(locale, _("A merge code hands this account over, so it is never posted in a channel.")),
            locale=locale,
        )

    @account_group.command(name="merge")
    @app_commands.describe(code=app_commands.locale_str(_("A code from `/account merge-code` on your other account.")))
    async def merge(self, ctx: Context[BotT], code: str) -> None:
        """Absorb another account you hold into this one."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        account_id = await ensure_consented_account(ctx, self.account_service, locale=locale)
        if account_id is None:
            return
        preview = await self.account_service.preview_merge(account_id, code)

        view = ConfirmationView(
            t(
                locale,
                _(
                    "Merging will move {aliases} creator name(s) and {identities} linked account(s) "
                    "onto this account, along with {builds} build credit(s).\n\n"
                    "This cannot be undone: the other account's creator page will permanently "
                    "redirect here."
                ),
                aliases=len(preview.alias_names),
                identities=preview.identity_count,
                builds=preview.build_count,
            ),
            locale=locale,
        )
        await ctx.send(view=view, ephemeral=personal(ctx), allowed_mentions=no_mentions())
        await view.wait()

        if view.value is None:
            message = t(locale, _("The confirmation expired, so nothing was merged."))
        elif not view.value:
            message = t(locale, _("Cancelled. Nothing was merged."))
        else:
            merge = await self.account_service.complete_merge(account_id, code)
            message = t(
                locale,
                _("Merged. `{redirected}` now redirects to your creator page."),
                redirected=merge.redirected_public_creator_id,
            )
        await ctx.send(view=text_layout(message), ephemeral=personal(ctx), allowed_mentions=no_mentions())

    @autocompletes(name="creators")
    @account_group.command(name="claim")
    @app_commands.describe(name=app_commands.locale_str(_("A creator name credited on builds you worked on.")))
    async def claim(self, ctx: Context[BotT], *, name: str) -> None:
        """Ask staff to credit you with an older creator name."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        account_id = await ensure_consented_account(ctx, self.account_service, locale=locale)
        if account_id is None:
            return
        claim = await self.account_service.request_alias_claim(account_id, name)
        await ctx.send(
            view=text_layout(
                t(
                    locale,
                    _("Claim #{id} for **{name}** is awaiting staff approval."),
                    id=claim.id,
                    name=claim.alias_name,
                )
            ),
            ephemeral=personal(ctx),
            allowed_mentions=no_mentions(),
        )

    @account_group.command(name="claims")
    @requires(ACCOUNT_CLAIM_LIST)
    async def pending_claims(self, ctx: Context[BotT]) -> None:
        """Review the creator credit claims awaiting a decision."""
        locale = await resolve_locale(ctx, self.bot.services.settings)
        claims = await self.account_service.pending_alias_claims(with_claimants=True)
        subject = await subject_for(ctx)
        approve, reject = await self.bot.services.permissions.decisions(
            subject, (ACCOUNT_CLAIM_APPROVE, ACCOUNT_CLAIM_REJECT)
        )
        component = ClaimReviewComponent(
            self.account_service,
            claims,
            author_id=ctx.author.id,
            locale=locale,
            can_approve=approve.allowed,
            can_reject=reject.allowed,
        )
        mount = component.mount()
        rendered = mount.build_view()
        message = await ctx.send(
            view=rendered,
            files=mount.attachment_files(),
            ephemeral=personal(ctx),
            allowed_mentions=no_mentions(),
        )
        mount.bind(message, rendered)


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

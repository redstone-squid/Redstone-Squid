"""Discord components for obtaining informed user-data consent."""

from typing import Any, override

import discord

from squid.accounts.domain import CURRENT_CONSENT_VERSION, AccountConsent, LinkPreview
from squid.bot.errors import ExpiringLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import CardField, card_container, edit_interaction_layout, no_mentions
from squid.core.i18n import _, ntranslate

PRIVACY_NOTICE = _(
    "Redstone Squid stores your Discord user ID, your Minecraft UUID and your current Minecraft "
    "username. The pair is what lets the bot recognise you as a build creator and keep the two "
    "accounts associated.\n\n"
    "Linking also claims build credit recorded under your verified Minecraft username, so those "
    "builds are attributed to your account. Credit already claimed by someone else is never taken "
    "from them; agreeing opens a claim for staff to review instead.\n\n"
    "Agreeing records which version of this notice you accepted and when. Cancelling stores no "
    "account information at all."
)
"""The full notice, kept out of the card and reachable from its own button.

One message rather than several so the version recorded in a consent receipt refers to a single
piece of text. The card still names the stored categories itself: consent is not informed if every
category is behind a button.
"""


class UserDataConsentView(ExpiringLayoutView):
    """Ask one Discord user to accept the current account-link privacy notice.

    Built around a *preview* rather than prose. The prompt used to describe categories of data
    because it ran before the code was redeemed and could not know anything concrete; a held code
    means it can name the Minecraft account, the credit at stake and the receipt it will write, which
    is what makes the decision an informed one rather than a policy to skim.
    """

    actions = discord.ui.ActionRow()

    def __init__(
        self,
        user_id: int,
        preview: LinkPreview,
        *,
        locale: str | None = None,
        timeout: float = 120.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.preview = preview
        self.locale = locale
        self.consent: AccountConsent | None = None
        controls = self.actions
        self.clear_items()
        self.add_item(
            card_container(
                t(locale, _("Link {username} to your Discord account"), username=preview.username),
                t(
                    locale,
                    _(
                        "Agreeing stores your Discord user ID, your Minecraft UUID and your current "
                        "Minecraft username, and records this consent. Cancelling stores nothing."
                    ),
                ),
                fields=self._fields(locale),
            )
        )
        self.add_item(controls)
        self.accept.label = t(locale, _("Agree and link"))
        self.cancel.label = t(locale, _("Cancel"))
        self.privacy.label = t(locale, _("Privacy notice"))

    def _fields(self, locale: str | None) -> tuple[CardField, ...]:
        """Lay out exactly what redeeming this code will write."""
        return (
            CardField(
                t(locale, _("Minecraft account")),
                t(
                    locale,
                    _("**{username}**\n`{uuid}`"),
                    username=self.preview.username,
                    uuid=self.preview.java_uuid,
                ),
            ),
            CardField(
                t(locale, _("Discord account")),
                t(locale, _("<@{user_id}> (`{user_id}`)"), user_id=self.user_id),
            ),
            CardField(t(locale, _("Build credit")), self._credit_value(locale)),
            CardField(
                t(locale, _("Consent recorded")),
                t(locale, _("Notice `{version}`, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
            ),
        )

    def _credit_value(self, locale: str | None) -> str:
        """Say what happens to the creator credit, including when nothing happens."""
        credit = self.preview.credit
        if credit is None:
            return t(
                locale,
                _("No build credits **{username}** yet, so nothing is reattributed."),
                username=self.preview.username,
            )

        builds = ntranslate(
            locale,
            _("{count} build"),
            _("{count} builds"),
            credit.build_count,
            count=credit.build_count,
        )
        if credit.is_contested:
            # Naming the outcome up front, because this is the case where agreeing does *not* do the
            # thing the rest of the card implies.
            return t(
                locale,
                _(
                    "**{name}** ({builds}) is already credited to another creator, so agreeing moves "
                    "nothing and opens a claim for staff to review."
                ),
                name=credit.name,
                builds=builds,
            )
        return t(
            locale,
            _("**{name}** ({builds}) becomes attributed to your account."),
            name=credit.name,
            builds=builds,
        )

    @override
    async def interaction_check(self, interaction: discord.Interaction[Any], /) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            t(self.locale, _("Only the person linking the account can answer this prompt.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @actions.button(label="Agree and link", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        self.consent = AccountConsent.grant_current()
        await self._finish(interaction)

    @actions.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        await self._finish(interaction)

    @actions.button(label="Privacy notice", style=discord.ButtonStyle.secondary)
    async def privacy(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        """Show the full notice without answering the prompt either way."""
        await interaction.response.send_message(
            t(self.locale, PRIVACY_NOTICE),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )

    async def _finish(self, interaction: discord.Interaction[Any]) -> None:
        """Render the prompt inert before releasing the waiting command."""
        for child in self.walk_children():
            if isinstance(child, discord.ui.Button | discord.ui.Select):
                child.disabled = True
        await edit_interaction_layout(interaction, self)
        self.stop()

    @property
    def notice_version(self) -> str:
        """Return the privacy notice version presented by this view."""
        return CURRENT_CONSENT_VERSION

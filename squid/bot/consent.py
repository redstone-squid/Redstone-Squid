"""Discord components for obtaining informed user-data consent."""

from typing import Any, override

import discord

from squid.bot.errors import ErrorHandledLayoutView
from squid.bot.i18n import t
from squid.bot.utils.components import no_mentions
from squid.core.i18n import _
from squid.users.domain import CURRENT_CONSENT_VERSION, UserConsent


class UserDataConsentView(ErrorHandledLayoutView):
    """Ask one Discord user to accept the current account-link privacy notice."""

    actions = discord.ui.ActionRow()

    def __init__(self, user_id: int, *, locale: str | None = None, timeout: float = 120.0) -> None:
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.consent: UserConsent | None = None
        controls = self.actions
        self.clear_items()
        self.add_item(
            discord.ui.TextDisplay(
                t(
                    locale,
                    _(
                        "## Link account and store my information\n"
                        "To link your accounts, Redstone Squid will store your Discord user ID, Minecraft UUID, "
                        "and current Minecraft username. This information is used to identify you as a build creator "
                        "and keep your Discord and Minecraft accounts linked.\n\n"
                        "Linking also claims any existing build credit under your verified Minecraft username, "
                        "so those builds are attributed to your account. Credits already claimed by someone else "
                        "are left alone; you can ask staff to review them with `/account claim`.\n\n"
                        "Selecting **Agree and link** records the notice version and time of your consent. "
                        "Selecting **Cancel** stores no user account information."
                    ),
                )
            )
        )
        self.add_item(controls)
        self.accept.label = t(locale, _("Agree and link"))
        self.cancel.label = t(locale, _("Cancel"))

    @override
    async def interaction_check(self, interaction: discord.Interaction[Any], /) -> bool:
        if interaction.user.id == self.user_id:
            return True
        await interaction.response.send_message(
            t(None, _("Only the person linking the account can answer this prompt.")),
            ephemeral=True,
            allowed_mentions=no_mentions(),
        )
        return False

    @actions.button(label="Agree and link", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer()
        self.consent = UserConsent.grant_current()
        self.stop()

    @actions.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction[Any], button: discord.ui.Button[Any]) -> None:
        await interaction.response.defer()
        self.stop()

    @property
    def notice_version(self) -> str:
        """Return the privacy notice version presented by this view."""
        return CURRENT_CONSENT_VERSION

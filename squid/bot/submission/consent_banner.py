"""Build log ingestion consent banner and ephemeral permission flow."""

import logging
from typing import TYPE_CHECKING, Any, Self, override

import discord
from discord import Interaction, TextChannel
from discord.ui import Item

from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.bot.consent import ConsentPromptView
from squid.bot.i18n import resolve_locale, t
from squid.bot.utils.components import StaticLayout, no_mentions, text_layout
from squid.bot.utils.sticky_message import StickyMessage
from squid.core.i18n import _

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    import squid.bot.app

logger = logging.getLogger(__name__)

CONSENT_BUTTON_CUSTOM_ID = "build_log:consent"


class DynamicBuildLogConsentButton[
    BotT: "squid.bot.app.RedstoneSquid",
    V: discord.ui.LayoutView,
](discord.ui.DynamicItem[discord.ui.Button[V]], template=r"build_log:consent"):
    """Public button on the channel consent banner opening an ephemeral consent prompt."""

    def __init__(self) -> None:
        super().__init__(
            discord.ui.Button(
                label="Enable Build Ingestion",
                style=discord.ButtonStyle.primary,
                custom_id=CONSENT_BUTTON_CUSTOM_ID,
                emoji="📋",
            )
        )

    @classmethod
    @override
    async def from_custom_id(  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        cls: type[Self], interaction: Interaction[BotT], item: Item[Any], match: Any, /
    ) -> Self:
        return cls()

    @override
    async def callback(  # pyright: ignore [reportIncompatibleMethodOverride]  # pyrefly: ignore[bad-override]
        self, interaction: Interaction[BotT]
    ) -> Any:
        await interaction.response.defer(ephemeral=True)
        locale = await resolve_locale(interaction, interaction.client.services.settings)
        accounts = interaction.client.services.accounts

        account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(interaction.user.id))
        if account is not None and account.id is not None and not account.needs_consent_refresh:
            await interaction.followup.send(
                view=text_layout(
                    t(
                        locale,
                        _(
                            "### Consent Already Granted\n"
                            "Your account has already accepted the current privacy notice. Redstone Squid automatically "
                            "indexes your submissions in build log channels.\n\n"
                            "*Tip:* If you posted a build before your consent was recorded, right-click that message and "
                            "select **Apps > Recalculate Build** to index it now."
                        ),
                    )
                ),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )
            return

        view = BuildLogConsentPromptView(interaction.user.id, locale=locale)
        msg = await interaction.followup.send(
            view=view,
            ephemeral=True,
            wait=True,
            allowed_mentions=no_mentions(),
        )
        view.bind_message(msg)
        await view.wait()

        if view.consent is not None:
            await accounts.get_or_create_identity(
                IdentityProvider.DISCORD, str(interaction.user.id), consent=view.consent
            )
            await interaction.followup.send(
                view=text_layout(
                    t(
                        locale,
                        _(
                            "### Consent Recorded!\n"
                            "Thank you! Your consent has been recorded under notice `{version}`. Your future builds "
                            "posted in build-log channels will now be automatically ingested and submitted for voting.\n\n"
                            "*Tip:* To ingest a build you posted recently, right-click your message and select "
                            "**Apps > Recalculate Build**."
                        ),
                        version=CURRENT_CONSENT_VERSION,
                    )
                ),
                ephemeral=True,
                allowed_mentions=no_mentions(),
            )


class BuildLogConsentPromptView(ConsentPromptView):
    """Customized consent card highlighting build log ingestion permissions."""

    @override
    def _title(self, locale: str | None) -> str:
        return t(locale, _("Enable Automatic Build Ingestion"))

    @override
    def _summary(self, locale: str | None) -> str:
        return t(
            locale,
            _(
                "Redstone Squid automatically indexes redstone doors and builds posted in this channel. "
                "Agreeing stores your Discord user ID and records this consent, allowing the bot to attribute "
                "your builds, mirror media, and analyze attached schematics. Cancelling stores nothing and leaves "
                "your posts ignored by automated ingestion."
            ),
        )

    @override
    def _accept_label(self, locale: str | None) -> str:
        return t(locale, _("Agree & Enable Ingestion"))


class BuildLogConsentStickyMessage(StickyMessage):
    """Sticky banner posted in build-log channels when unconsented users post."""

    @override
    async def render(self, channel: TextChannel) -> discord.ui.LayoutView:
        return StaticLayout(
            discord.ui.TextDisplay(
                "## 📋 Build Log Ingestion Consent\n"
                "Redstone Squid automatically indexes and tracks redstone door and build submissions in this channel. "
                "To attribute your builds, parse schematics, and record your scores, the bot requires your consent to store "
                "your Discord user ID.\n\n"
                "Messages from unconsented users are not ingested. Click below to review permissions and enable automated ingestion."
            ),
            discord.ui.ActionRow(DynamicBuildLogConsentButton()),
        )

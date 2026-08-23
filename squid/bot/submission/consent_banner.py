"""Build log ingestion consent banner and ephemeral permission flow."""

import logging
from typing import TYPE_CHECKING, override

from discord import Interaction, TextChannel

import squid_layouts as sl
from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.bot.consent import CONSENT_SCREEN, ConsentPrompt
from squid.bot.i18n import resolve_locale, t
from squid.bot.routes.build_log_consents import build_log_consent, build_log_consents
from squid.bot.ui import (
    CardField,
    localization_for,
    render_presentation,
    respond_presentation,
    text_layout,
)
from squid.bot.utils.sticky_message import StickyMessage
from squid.core.i18n import _
from squid_layouts.discord.sessions import Opened, Rejected

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)

CONSENT_BUTTON_CUSTOM_ID = build_log_consent.id()


@build_log_consents.route(build_log_consent)
async def open_consent_prompt(interaction: Interaction[RedstoneSquid]) -> None:
    """Open the ephemeral consent prompt behind the public banner button."""
    locale = await resolve_locale(interaction, interaction.client.services.settings)
    accounts = interaction.client.services.accounts

    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(interaction.user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        await respond_presentation(
            interaction,
            text_layout(
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
        )
        return

    component = ConsentPrompt(
        user_id=interaction.user.id,
        title=t(locale, _("Enable Automatic Build Ingestion")),
        summary=t(
            locale,
            _(
                "Redstone Squid automatically indexes redstone doors and builds posted in this channel. "
                "Agreeing stores your Discord user ID and records this consent, allowing the bot to attribute "
                "your builds, mirror media, and analyze attached schematics. Cancelling stores nothing and leaves "
                "your posts ignored by automated ingestion."
            ),
        ),
        fields=(
            CardField(
                t(locale, _("Discord account")),
                t(locale, _("<@{user_id}> ({user_id})"), user_id=interaction.user.id),
            ),
            CardField(
                t(locale, _("Consent recorded")),
                t(locale, _("Notice {version}, timed at the moment you agree."), version=CURRENT_CONSENT_VERSION),
            ),
        ),
        accept_label=t(locale, _("Agree & Enable Ingestion")),
        locale=locale,
        timeout=120,
    )
    registry = interaction.client.mounts
    opened = await CONSENT_SCREEN.respond(
        registry,
        component,
        interaction,
        wait=True,
        localization=localization_for(locale),
    )
    if isinstance(opened, Rejected):
        await respond_presentation(
            interaction,
            text_layout(t(locale, _("You already have a consent prompt open. Please answer that one."))),
        )
        return
    if not isinstance(opened, Opened):
        return
    await component.wait()

    if component.consent is not None:
        await accounts.get_or_create_identity(
            IdentityProvider.DISCORD, str(interaction.user.id), consent=component.consent
        )
        await respond_presentation(
            interaction,
            text_layout(
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
        )


class BuildLogConsentStickyMessage(StickyMessage):
    """Sticky banner posted in build-log channels when unconsented users post."""

    @override
    async def render(self, channel: TextChannel) -> sl.discord.presentation.DiscordPresentation:
        return render_presentation(
            [
                sl.primitives.Text(
                    "## \U0001f4cb Build Log Ingestion Consent\n"
                    "Redstone Squid automatically indexes and tracks redstone door and build submissions in this channel. "
                    "To attribute your builds, parse schematics, and record your scores, the bot requires your consent to store "
                    "your Discord user ID.\n\n"
                    "Messages from unconsented users are not ingested. Click below to review permissions and enable automated ingestion."
                ),
                sl.primitives.Row(
                    (
                        sl.primitives.RoutedButton(
                            "Enable Build Ingestion",
                            CONSENT_BUTTON_CUSTOM_ID,
                            style=sl.primitives.ActionStyle.PRIMARY,
                            emoji="\N{CLIPBOARD}",
                        ),
                    )
                ),
            ]
        )

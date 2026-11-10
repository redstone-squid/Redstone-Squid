"""Build log ingestion consent banner and ephemeral permission flow."""

import logging
from typing import TYPE_CHECKING, override

from discord import Interaction, TextChannel

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.bot.consent import ConsentPrompt
from squid.bot.routes._root import _feature_group
from squid.bot.ui import CardField, render_payload, text_node, tr
from squid.bot.utils.sticky_message import StickyMessage

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)

build_log_consents, _consents_created = _feature_group("build-log-consents")
build_log_consent = build_log_consents.define("new", aliases=("build_log:consent",))

CONSENT_BUTTON_CUSTOM_ID = build_log_consent.id()


@build_log_consents.route(build_log_consent)
async def open_consent_prompt(interaction: Interaction[RedstoneSquid]) -> None:
    """Open the ephemeral consent prompt behind the public banner button."""
    request = await sd.request(interaction)
    accounts = interaction.client.services.accounts

    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(interaction.user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        await request.respond(
            text_node(
                tr(t"""
                    ingestion-consent-already-granted =
                        ### Consent Already Granted
                        Your account has already accepted the current privacy notice. Redstone Squid automatically indexes your submissions in build log channels.

                        {{ "*" }}Tip:* If you posted a build before your consent was recorded, right-click that message and select **Apps > Recalculate Build** to index it now.
                    """)
            ),
            audience="personal",
        )
        return

    user_id = interaction.user.id
    version = CURRENT_CONSENT_VERSION
    component = ConsentPrompt(
        user_id=user_id,
        title=tr(t"ingestion-enable-automatic-action = Enable Automatic Build Ingestion"),
        summary=tr(
            t"ingestion-consent-data-use-notice = "
            t"Redstone Squid automatically indexes redstone doors and builds posted in this channel. "
            t"Agreeing stores your Discord user ID and records this consent, allowing the bot to attribute "
            t"your builds, mirror media, and analyze attached schematics. Cancelling stores nothing and "
            t"leaves your posts ignored by automated ingestion."
        ),
        fields=(
            CardField(
                tr(t"bot-submission-consent-banner-discord-account = Discord account"),
                tr(
                    t"bot-submission-consent-banner-value = <@{(user_id_text := str(user_id))}> ({(user_id_text := str(user_id))})"  # noqa: F841
                ),
            ),
            CardField(
                tr(t"bot-submission-consent-banner-consent-recorded = Consent recorded"),
                tr(t"ingestion-consent-timestamp-notice = Notice {version}, timed at the moment you agree."),
            ),
        ),
        accept_label=tr(t"ingestion-consent-accept-action = Agree & Enable Ingestion"),
    )
    outcome = await request.respond(component)
    if not isinstance(outcome, sd.Presented):
        return
    result = await component.wait()

    if isinstance(result, sd.Completed):
        await accounts.get_or_create_identity(IdentityProvider.DISCORD, str(interaction.user.id), consent=result.value)
        await request.respond(
            text_node(
                tr(t"""
                    ingestion-consent-recorded-confirmation =
                        ### Consent Recorded!
                        Thank you! Your consent has been recorded under notice `{version}`. Your future builds posted in build-log channels will now be automatically ingested and submitted for voting.

                        {{ "*" }}Tip:* To ingest a build you posted recently, right-click your message and select **Apps > Recalculate Build**.
                    """)
            ),
            audience="personal",
        )


class BuildLogConsentStickyMessage(StickyMessage):
    """Sticky banner posted in build-log channels when unconsented users post."""

    @override
    async def render(self, channel: TextChannel) -> sd.message_payload.MessagePayload:
        return render_payload(
            [
                sd.v2.text(
                    "## \U0001f4cb Build Log Ingestion Consent\n"
                    "Redstone Squid automatically indexes and tracks redstone door and build submissions in this channel. "
                    "To attribute your builds, parse schematics, and record your scores, the bot requires your consent to store "
                    "your Discord user ID.\n\n"
                    "Messages from unconsented users are not ingested. Click below to review permissions and enable automated ingestion."
                ),
                sd.v2.row(
                    sd.v2.routed_button(
                        "Enable Build Ingestion",
                        CONSENT_BUTTON_CUSTOM_ID,
                        style=sl.primitives.ActionStyle.PRIMARY,
                        emoji="\N{CLIPBOARD}",
                    ),
                ),
            ]
        )

"""Build log ingestion consent banner and ephemeral permission flow."""

import logging
from typing import TYPE_CHECKING, override

from discord import Interaction, TextChannel

import squid_ui as sl
import squid_ui_discord as sd
from squid.accounts.domain import CURRENT_CONSENT_VERSION, IdentityProvider
from squid.bot.consent import ConsentPrompt
from squid.bot.routes._root import _feature_group, _feature_route
from squid.bot.ui import CardField, render_payload, text_node, tr
from squid.bot.utils.sticky_message import StickyMessage

if TYPE_CHECKING:
    # importing this causes a circular import at runtime
    from squid.bot.app import RedstoneSquid

logger = logging.getLogger(__name__)

build_log_consents, _consents_created = _feature_group("build-log-consents")
build_log_consent = _feature_route(build_log_consents, "new", aliases=("build_log:consent",))

CONSENT_BUTTON_CUSTOM_ID = build_log_consent.id()


@build_log_consents.route(build_log_consent)
async def open_consent_prompt(interaction: Interaction[RedstoneSquid]) -> None:
    """Open the ephemeral consent prompt behind the public banner button."""
    invocation = await sd.Invocation.of(interaction)
    accounts = interaction.client.services.accounts

    account = await accounts.get_account_by_identity(IdentityProvider.DISCORD, str(interaction.user.id))
    if account is not None and account.id is not None and not account.needs_consent_refresh:
        await invocation.reply(
            text_node(
                tr(
                    t"### Consent Already Granted\n"
                    t"Your account has already accepted the current privacy notice. Redstone Squid automatically "
                    t"indexes your submissions in build log channels.\n\n"
                    t"*Tip:* If you posted a build before your consent was recorded, right-click that message and "
                    t"select **Apps > Recalculate Build** to index it now."
                )
            ),
            visibility="personal",
        )
        return

    user_id = interaction.user.id
    version = CURRENT_CONSENT_VERSION
    component = await ConsentPrompt(
        user_id=user_id,
        title=tr(t"Enable Automatic Build Ingestion"),
        summary=tr(
            t"Redstone Squid automatically indexes redstone doors and builds posted in this channel. "
            t"Agreeing stores your Discord user ID and records this consent, allowing the bot to attribute "
            t"your builds, mirror media, and analyze attached schematics. Cancelling stores nothing and leaves "
            t"your posts ignored by automated ingestion."
        ),
        fields=(
            CardField(
                tr(t"Discord account"),
                tr(t"<@{user_id}> ({user_id})"),
            ),
            CardField(
                tr(t"Consent recorded"),
                tr(t"Notice {version}, timed at the moment you agree."),
            ),
        ),
        accept_label=tr(t"Agree & Enable Ingestion"),
        wait_timeout=120,
    ).show(invocation, wait=True)
    if component is None:
        return
    await component.wait()

    if component.consent is not None:
        await accounts.get_or_create_identity(
            IdentityProvider.DISCORD, str(interaction.user.id), consent=component.consent
        )
        await invocation.reply(
            text_node(
                tr(
                    t"### Consent Recorded!\n"
                    t"Thank you! Your consent has been recorded under notice `{version}`. Your future builds "
                    t"posted in build-log channels will now be automatically ingested and submitted for voting.\n\n"
                    t"*Tip:* To ingest a build you posted recently, right-click your message and select "
                    t"**Apps > Recalculate Build**.",
                )
            ),
            visibility="personal",
        )


class BuildLogConsentStickyMessage(StickyMessage):
    """Sticky banner posted in build-log channels when unconsented users post."""

    @override
    async def render(self, channel: TextChannel) -> sd.message_payload.MessagePayload:
        return render_payload(
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

"""Ingest consenting build-log messages and prepare recalculation proposals."""

import logging
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid4, uuid5

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.bot.routes import AppRequest
from squid.bot.submission.consent_banner import BuildLogConsentStickyMessage
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.submission.ui import controls as build_controls
from squid.bot.ui import error_node, text_node
from squid.bot.utils.permissions import enforce
from squid.bot.utils.sticky_message import StickyMessage
from squid.core.accounts.domain import IdentityProvider
from squid.core.i18n import tr
from squid.core.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.core.submissions.errors import DraftCapacityExceededError
from squid_i18n import md_tr
from squid_ui.ext.discord import primitives

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

# Disable the consent banner without disabling ingestion for consenting accounts.
# The bool annotation keeps type checkers from treating the guarded branches as unreachable.
CONSENT_STICKY_ENABLED: bool = False


class BuildMessageIntake[BotT: squid.bot.app.RedstoneSquid]:
    """Handle build-log ingestion and message recalculation."""

    def __init__(self, bot: BotT) -> None:
        self.bot = bot
        self.consent_sticky: StickyMessage = BuildLogConsentStickyMessage(bot.background_tasks)

    async def close(self) -> None:
        """Stop pending consent-banner updates when the cog unloads."""
        await self.consent_sticky.close()

    async def _is_build_log_message(self, message: discord.Message) -> bool:
        """Check the shared author/channel eligibility rule for ingestion and recalculation."""
        if message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return False
        settings = await self.bot.services.settings.get_runtime()
        return message.channel.id in settings.build_log_channel_ids

    async def infer_build_from_message(self, message: discord.Message) -> None:
        """Ingest an eligible build log message when its author has current consent."""
        if not await self._is_build_log_message(message):
            return
        assert isinstance(message.channel, discord.TextChannel)
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        # ORM identity columns may still be None before flush.
        if (
            account is None
            or account.id is None  # pyright: ignore[reportUnnecessaryComparison]
            or account.needs_consent_refresh
        ):
            logger.debug(
                "Skipping build inference for unconsented author %s in channel %s",
                message.author.id,
                message.channel.id,
            )
            if CONSENT_STICKY_ENABLED:
                await self.consent_sticky.trigger(message.channel)
            return

        if CONSENT_STICKY_ENABLED:
            self.consent_sticky.record_activity(message.channel.id)
        preceding = [item async for item in message.channel.history(before=message, limit=3)]
        preceding.reverse()
        try:
            run_id = await ingest_message_bundle(
                [message],
                preceding,
                self.bot.services,
                model=self.bot.inference_model,
                reasoning_effort=self.bot.inference_reasoning_effort,
            )
        except DraftCapacityExceededError:
            logger.warning(
                "Automated submission intake is full; skipped message %s in channel %s", message.id, message.channel.id
            )
            return
        await self.bot.refresh_posts("inference_run", str(run_id))

    async def recalculate(self, request: AppRequest, message: discord.Message) -> sd.CommandResult:
        """Review proposed build corrections inferred from this message."""
        await enforce(request, BUILD_SUBMISSION_RECALC)
        if not await self._is_build_log_message(message):
            return error_node(
                tr(t"bot-submission-submit-nothing-to-recalculate = Nothing to recalculate"),
                tr(
                    t"submit-build-log-channel-required = Builds are only read "
                    t"out of messages posted in a build log channel."
                ),
            )

        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if (
            account is None
            or account.id is None  # pyright: ignore[reportUnnecessaryComparison]
            or account.needs_consent_refresh
        ):
            user_id = message.author.id
            if CONSENT_STICKY_ENABLED and isinstance(message.channel, discord.TextChannel):
                await self.consent_sticky.trigger(message.channel)
            return error_node(
                tr(t"bot-submission-submit-author-has-not-consented = Author has not consented"),
                md_tr(
                    t"submit-author-consent-required = The author of this "
                    t"message ({(user_mention := sl.mention(user_id))}) has not consented to data storage. They must grant consent before "  # noqa: F841 - captured text identifier
                    t"this build can be ingested.",
                ),
            )

        return await self.propose_recalculation(request, message, owner_account_id=account.id)

    async def propose_recalculation(
        self,
        request: AppRequest,
        message: discord.Message,
        *,
        owner_account_id: int,
    ) -> sd.CommandResult:
        """Retain inferred candidates and return review controls without submitting builds."""
        actor = await request.client.actors.resolve(request)
        run_id = (
            uuid5(NAMESPACE_URL, f"discord-recalculation:{request.interaction.id}")
            if request.interaction is not None
            else uuid4()
        )
        bundle = await assemble_bundle([message], preceding=(), include_images=True)
        candidates = await self.bot.services.submission_inference.infer(
            run_id,
            owner_account_id,
            bundle,
            purpose="recalculation",
            model=self.bot.inference_model,
            reasoning_effort=self.bot.inference_reasoning_effort,
        )
        proposals = [
            await self.bot.services.submission_recalculations.create(
                run_id=run_id,
                index=index,
                owner_account_id=owner_account_id,
                actor=actor,
                source_message_id=message.id,
                candidate=candidate.facts,
            )
            for index, candidate in enumerate(candidates)
        ]
        if not proposals:
            return text_node("No build candidates were inferred. Existing builds were preserved.")
        return tuple(
            primitives.Section(
                (primitives.Text(f"Recalculation candidate {index + 1}: review and choose its target build."),),
                primitives.RoutedButton(
                    "Review changes", build_controls.reopen_revision.custom_id(proposal_id=proposal.id)
                ),
            )
            for index, proposal in enumerate(proposals)
        )

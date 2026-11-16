"""List saved drafts and ingest consenting authors' build-log messages.

Message inference and candidate admission live in ingestion.py; submission
services decide when a retained draft is ready to become a build.
"""

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Self, override
from uuid import NAMESPACE_URL, uuid4, uuid5

import discord

import squid_ui as sl
import squid_ui.ext.discord as sd
from squid.accounts.domain import IdentityProvider
from squid.bot.accounts.consent import ensure_consented_account
from squid.bot.i18n import to_locale_str
from squid.bot.routes import AppRequest, ScreenUnavailable, router
from squid.bot.submission.ingestion import ingest_message_bundle
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.submission.ui import controls as build_controls
from squid.bot.submission.ui.drafts import DraftEditorScreen
from squid.bot.submission.ui.inference import reopen_inference
from squid.bot.ui import error_node, text_node
from squid.bot.utils.permissions import enforce
from squid.bot.utils.sticky_message import StickyMessage
from squid.builds.application import (
    BuildInferenceService,
    BuildService,
)
from squid.core.i18n import tr
from squid.messages.application import MessageService
from squid.permissions.domain.catalogue import BUILD_SUBMISSION_RECALC
from squid.submissions.application import StoredDraft
from squid.submissions.domain import (
    DraftStatus,
)
from squid.submissions.errors import DraftCapacityExceededError
from squid_i18n import Text, md_tr
from squid_ui.content.text import md

if TYPE_CHECKING:
    import squid.bot.app

logger = logging.getLogger(__name__)

# Disable the consent banner without disabling ingestion for consenting accounts.
# The bool annotation keeps type checkers from treating the guarded branches as unreachable.
CONSENT_STICKY_ENABLED: bool = False


class SavedDraftsScreen(sd.Screen):
    """Keep saved drafts and inference runs reachable across Discord message pages."""

    def __init__(self, nodes: Sequence[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]]) -> None:
        self.nodes = tuple(nodes)

    @router.route("/drafts")
    @classmethod
    async def open_drafts(cls, request: AppRequest, inbox: bool = False) -> Self | None:
        """List the opener's drafts, or the staff correction inbox when `inbox` is set."""
        account_id = await ensure_consented_account(request, request.client.services.accounts)
        if account_id is None:
            return None
        actor = await request.client.actors.resolve(request)
        drafts = (
            await request.client.services.submission_drafts.attention_inbox(actor, limit=10)
            if inbox
            else await request.client.services.submission_drafts.list_active(account_id)
        )
        runs = await request.client.services.submission_inference.list_active(actor, inbox=inbox)
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = [
            sl.primitives.Section(
                (sl.primitives.Text(_draft_line(draft)),),
                sl.primitives.RoutedButton(
                    tr(t"build-drafts-open = Open"),
                    DraftEditorScreen.open_draft.custom_id(draft_id=draft.snapshot.id),
                ),
            )
            for draft in drafts
        ]
        nodes.extend(
            sl.primitives.Section(
                (sl.primitives.Text(md_tr(t"build-drafts-detected = **Builds found in your build-log posts**")),),
                sl.primitives.RoutedButton(
                    tr(t"build-drafts-review = Review"), reopen_inference.custom_id(run_id=run_id)
                ),
            )
            for run_id in runs
        )
        if nodes:
            return cls(nodes)
        if inbox:
            raise ScreenUnavailable(tr(t"build-drafts-inbox-empty = No drafts are waiting for staff correction."))
        raise ScreenUnavailable(
            md_tr(t"build-drafts-empty = You have no drafts in progress. Start one with `/build submit`.")
        )

    @override
    def render(self) -> sl.Document[sl.ComponentsV2Target, sd.Request]:
        return sl.Document(self.nodes, key="saved-drafts")


class BuildSubmitCommands[BotT: squid.bot.app.RedstoneSquid](sd.Cog[BotT]):
    """Handle build-log ingestion and message recalculation."""

    bot: BotT
    builds: BuildService
    inference: BuildInferenceService
    messages: MessageService
    consent_sticky: StickyMessage

    async def _is_build_log_message(self, message: discord.Message) -> bool:
        """Check the shared author/channel eligibility rule for ingestion and recalculation."""
        if message.author.bot or not isinstance(message.channel, discord.TextChannel):
            return False
        settings = await self.bot.services.settings.get_runtime()
        return message.channel.id in settings.build_log_channel_ids

    @sd.Cog.listener(name="on_message")
    async def infer_build_from_message(self, message: discord.Message) -> None:
        """Ingest an eligible build log message when its author has current consent."""
        if not await self._is_build_log_message(message):
            return
        assert isinstance(message.channel, discord.TextChannel)
        account = await self.bot.services.accounts.get_account_by_identity(
            IdentityProvider.DISCORD, str(message.author.id)
        )
        if account is None or account.id is None or account.needs_consent_refresh:
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

    @sd.context_menu(name=to_locale_str(tr(t"command-recalculate-build-name = Scan Build Message")), defer="private")
    async def recalc_context_menu(self, request: sd.Request[Self, BotT], message: discord.Message) -> sd.CommandResult:
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
        if account is None or account.id is None or account.needs_consent_refresh:
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
        request: sd.Request[Self, BotT],
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
            sl.primitives.Section(
                (sl.primitives.Text(f"Recalculation candidate {index + 1}: review and choose its target build."),),
                sl.primitives.RoutedButton(
                    "Review changes", build_controls.reopen_revision.custom_id(proposal_id=proposal.id)
                ),
            )
            for index, proposal in enumerate(proposals)
        )


def _draft_line(draft: StoredDraft) -> Text:
    """Describe a draft in the /build drafts list by category, a name, its state, and last edit."""
    answers = draft.snapshot.answers
    creators = answers.get("creators")
    name = answers.get("display_name") or (", ".join(map(str, creators)) if isinstance(creators, list) else None)
    category = draft.snapshot.category.capitalize()
    title = md(t"**{category}** · {name}") if name else md(t"**{category}**")
    match draft.snapshot.status:
        case DraftStatus.PROCESSING:
            state = tr(t"build-drafts-state-creating = Creating the build")
        case DraftStatus.NEEDS_ATTENTION if draft.preparation_retry_at is not None:
            state = tr(t"build-drafts-state-waiting = Waiting for files")
        case DraftStatus.NEEDS_ATTENTION:
            state = tr(t"build-drafts-state-needs-changes = Needs changes")
        case _:
            state = tr(t"build-drafts-state-editing = In progress")
    updated = draft.updated_at.to_stdlib()
    edited = md_tr(t"build-drafts-edited = Edited {updated}")
    return Text(t"{title}\n{state} · {edited}")

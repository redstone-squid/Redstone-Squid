"""Persist inferred message candidates and hand complete drafts to shared finalization."""

import logging
from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import discord

from squid.bot.submission.draft_intake import receive_retained_file
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.utils.accounts import account_id_for
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.submissions.application.inferred_drafts import materialize_candidate
from squid.submissions.domain import DraftStatus
from squid.submissions.domain.source_files import SubmissionSourceFile

logger = logging.getLogger(__name__)


async def ingest_message_bundle(
    primary: Sequence[discord.Message],
    preceding: Sequence[discord.Message],
    services: BotServices,
    *,
    model: str,
    reasoning_effort: str | None = None,
    include_images: bool = True,
) -> UUID:
    """Retain candidates; ambiguous categories and file assignments require explicit correction."""
    run_id = uuid5(NAMESPACE_URL, "discord-inference:" + ":".join(str(message.id) for message in primary))
    owner = await account_id_for(services.accounts, primary[0].author)
    source_files = tuple(
        SubmissionSourceFile(
            uuid5(run_id, str(attachment.id)),
            attachment.filename,
            attachment.content_type,
            attachment.url,
            attachment.size,
        )
        for message in primary
        for attachment in message.attachments
    )
    bundle = await assemble_bundle(primary, preceding=preceding, include_images=include_images)
    candidates = await services.submission_inference.infer(
        run_id,
        owner,
        bundle,
        model=model,
        reasoning_effort=reasoning_effort,
        source_files=source_files,
    )
    for candidate in candidates:
        if candidate.facts.category is None:
            continue
        try:
            draft = await materialize_candidate(candidate, services.submission_drafts, services.submission_forms)
            # Replayed deliveries must not submit a draft that the owner has since corrected.
            if draft.snapshot.revision != 1 or draft.snapshot.status is not DraftStatus.EDITING:
                continue
            if len(candidates) > 1 and source_files:
                await services.submission_finalization.submit(candidate.id, owner, locale=None)
                continue
            for source in candidate.source_files:
                try:
                    await receive_retained_file(services, candidate.id, owner, source.id)
                except Exception:
                    logger.warning("Could not receive inferred source %s", source.id, exc_info=True)
            await services.submission_finalization.submit(candidate.id, owner, locale=None)
        except SquidError:
            logger.warning("Inferred candidate %s requires recovery", candidate.id, exc_info=True)
    return run_id

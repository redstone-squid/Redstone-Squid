"""Turn build-log messages into retained candidates and submission drafts.

An inference run can find several builds in one message bundle. Its candidates
are saved before admission turns them into editable drafts and requests shared
finalization. Unclear categories or file assignments remain available for user
review. Repeated delivery resumes the same run, and admission leaves drafts the
user has already edited alone.
"""

import logging
from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import discord

from squid.bot.submission.draft_intake import receive_retained_file
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.utils.accounts import get_or_create_discord_account_id
from squid.core.errors import SquidError
from squid.permissions.domain import Subject
from squid.runtime import BotServices
from squid.submissions.application.drafts import DraftActor
from squid.submissions.application.inference_runs import InferenceCandidate
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
    """Resume or infer candidates for a nonempty, ordered bundle of primary messages.

    The first primary author owns the run; callers check that author's consent before
    entering. Primary message IDs determine its retry identity. Admission may queue
    complete drafts, while missing categories and ambiguous files remain for review.
    """
    run_id = uuid5(NAMESPACE_URL, "discord-inference:" + ":".join(str(message.id) for message in primary))
    owner = await get_or_create_discord_account_id(services.accounts, primary[0].author)
    candidates = await services.submission_inference.resume(run_id, Subject(account_id=owner))
    if candidates is None:
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
    await admit_candidates(services, candidates)
    return run_id


async def admit_candidates(
    services: BotServices, candidates: tuple[InferenceCandidate, ...], *, actor: DraftActor | None = None
) -> None:
    """Try to submit untouched inferred drafts while preserving edits and unresolved files.

    Only drafts still at their initial inferred revision are admitted automatically.
    Files shared by multiple candidates require explicit assignment. Domain errors
    are logged per candidate so other candidates can continue.
    """
    for candidate in candidates:
        if candidate.facts.category is None:
            continue
        try:
            authority = actor if actor is not None else candidate.owner_account_id
            draft = await materialize_candidate(
                candidate, services.submission_drafts, services.submission_forms, actor=authority
            )
            # Replayed deliveries must not submit a draft that the owner has since corrected.
            if draft.snapshot.revision != 1 or draft.snapshot.status is not DraftStatus.EDITING:
                continue
            if len(candidates) > 1 and candidate.source_files:
                # Finalization records the unresolved assignment without guessing which draft owns each file.
                await services.submission_finalization.submit(candidate.id, authority, locale=None)
                continue
            for source in candidate.source_files:
                try:
                    await receive_retained_file(services, candidate.id, authority, source.id)
                except Exception:
                    logger.warning("Could not receive inferred source %s", source.id, exc_info=True)
            await services.submission_finalization.submit(candidate.id, authority, locale=None)
        except SquidError:
            logger.warning("Inferred candidate %s requires recovery", candidate.id, exc_info=True)

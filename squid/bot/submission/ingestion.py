"""Turn build-log messages into retained candidates and submission drafts.

An inference run can find several builds in one message bundle. Its candidates
are saved before admission turns them into editable drafts and requests shared
finalization. Unclear categories or file assignments remain available for user
review. Repeated delivery resumes the same run, and admission leaves drafts the
user has already edited alone.
"""

import logging
from collections import Counter
from collections.abc import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

import discord

from squid.bot.submission.draft_intake import receive_retained_file
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.utils.accounts import get_or_create_discord_account_id
from squid.core.actors import ActingContext, Actor
from squid.core.errors import SquidError
from squid.core.submissions.application.drafts import INGESTION_OPTIONAL_FIELDS
from squid.core.submissions.application.finalization import DraftPreparationSnapshot
from squid.core.submissions.application.inference_runs import InferenceCandidate
from squid.core.submissions.application.inferred_drafts import materialize_candidate
from squid.core.submissions.domain import DraftStatus, SubmissionAttentionIssue, SubmissionAttentionReason
from squid.core.submissions.domain.source_files import SubmissionSourceFile
from squid.core.submissions.errors import DraftCapacityExceededError
from squid.runtime import BotServices

logger = logging.getLogger(__name__)


def message_bundle_run_id(primary: Sequence[discord.Message]) -> UUID:
    """Identify a retained Discord inference run by its ordered primary messages."""
    return uuid5(NAMESPACE_URL, "discord-inference:" + ":".join(str(message.id) for message in primary))


async def ingest_message_bundle(
    primary: Sequence[discord.Message],
    preceding: Sequence[discord.Message],
    services: BotServices,
    *,
    model: str,
    reasoning_effort: str | None = None,
    include_images: bool = True,
    retry_failed: bool = False,
) -> UUID:
    """Resume or infer candidates for a nonempty, ordered bundle of primary messages.

    The first primary author owns the run; callers check that author's consent before
    entering. Primary message IDs determine its retry identity. Admission may queue
    complete drafts, while missing categories and ambiguous files remain for review.
    """
    run_id = message_bundle_run_id(primary)
    owner = await get_or_create_discord_account_id(services.accounts, primary[0].author)
    source = primary[0]
    author = Actor(
        account_id=owner, discord_user_id=source.author.id, guild_id=source.guild.id if source.guild else None
    )
    context = ActingContext.for_actor(author)
    with context.scope():
        candidates = await services.submission_inference.resume(run_id, context=context)
        if candidates is None:
            source_files = tuple(
                SubmissionSourceFile(
                    uuid5(run_id, str(attachment.id)),
                    attachment.filename,
                    attachment.content_type,
                    attachment.url,
                    attachment.size,
                    source_message_id=message.id,
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
                context=ActingContext.current(),
            )
        await admit_candidates(services, candidates, actor=context.effective, retry_failed=retry_failed)
    return run_id


async def admit_candidates(
    services: BotServices,
    candidates: tuple[InferenceCandidate, ...],
    *,
    actor: Actor,
    retry_failed: bool = False,
) -> None:
    """Try to submit untouched inferred drafts while preserving edits and unresolved files.

    Only drafts still at their initial inferred revision are admitted automatically.
    retry_failed also admits untouched drafts whose worker retries were exhausted.
    Untouched imported drafts blocked on missing metadata can also be retried.
    Shared files still need explicit assignment.
    """
    file_owners = Counter(
        source_id for candidate in candidates for source_id in {file.id for file in candidate.source_files}
    )
    for candidate in candidates:
        if candidate.facts.category is None:
            continue
        try:
            draft = await materialize_candidate(
                candidate, services.submission_drafts, services.submission_forms, actor=actor
            )
            # Replayed deliveries must not submit a draft that the owner has since corrected.
            metadata_retry = (
                retry_failed
                and draft.inferred
                and draft.inference_run_id is not None
                and draft.snapshot.status is DraftStatus.NEEDS_ATTENTION
                and bool(draft.preparation_issues)
                and all(
                    issue.reason is SubmissionAttentionReason.REQUIRED and issue.field_id in INGESTION_OPTIONAL_FIELDS
                    for issue in draft.preparation_issues
                )
            )
            retryable = (
                retry_failed
                and draft.snapshot.status is DraftStatus.NEEDS_ATTENTION
                and draft.preparation_issues
                == (SubmissionAttentionIssue("submission", SubmissionAttentionReason.RETRY_EXHAUSTED),)
            )
            if draft.snapshot.revision != 1 or (
                draft.snapshot.status is not DraftStatus.EDITING and not retryable and not metadata_retry
            ):
                logger.info(
                    "Preserved candidate %s: %s, revision %s; issues: %s",
                    candidate.id,
                    draft.snapshot.status.value,
                    draft.snapshot.revision,
                    ", ".join(f"{issue.field_id}: {issue.reason.value}" for issue in draft.preparation_issues)
                    or "none",
                )
                continue
            # Finalization records shared files as unresolved rather than guessing their assignment.
            for source in candidate.source_files:
                if file_owners[source.id] > 1:
                    continue
                try:
                    await receive_retained_file(services, candidate.id, actor, source.id)
                except Exception:
                    logger.warning("Could not receive inferred source %s", source.id, exc_info=True)
            outcome = await services.submission_finalization.submit(
                candidate.id, locale=None, context=ActingContext.for_actor(actor)
            )
            if isinstance(outcome, DraftPreparationSnapshot):
                logger.warning(
                    "Candidate %s needs attention: %s",
                    candidate.id,
                    ", ".join(f"{issue.field_id}: {issue.reason.value}" for issue in outcome.issues),
                )
            else:
                logger.info(
                    "Candidate %s finalization: %s (job %s)", candidate.id, outcome.status.value, outcome.job_id
                )
        except DraftCapacityExceededError:
            # Importers must stop and resume retained results after making room.
            raise
        except SquidError:
            logger.warning("Inferred candidate %s requires recovery", candidate.id, exc_info=True)

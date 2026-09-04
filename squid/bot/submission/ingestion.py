"""Shared ingestion of inferred Discord message bundles."""

import logging
from collections.abc import Sequence
from copy import deepcopy

import discord

from squid.bot.submission.attachment_enrichment import (
    AttachmentFailure,
    AttachmentLifecycle,
    attachment_failure_evidence,
    attachment_failure_for,
    default_only_usable,
    merge_duplicate_evidence,
)
from squid.bot.submission.attachments import classify_attachment
from squid.bot.submission.media import MediaMirror
from squid.bot.submission.message_context import assemble_bundle
from squid.bot.utils.accounts import account_id_for
from squid.builds.domain import Build, BuildCategory
from squid.builds.domain.models import AttachmentFailureInfo, SchematicDuplicateInfo
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.schematics.application import IngestRequest

logger = logging.getLogger(__name__)


async def ingest_message_bundle(
    primary: Sequence[discord.Message],
    preceding: Sequence[discord.Message],
    services: BotServices,
    *,
    model: str,
    mirror: MediaMirror,
    reasoning_effort: str | None = None,
    include_images: bool = True,
    dry_run: bool = False,
) -> list[Build]:
    """Infer, mirror, analyze, submit, and track one message bundle."""
    bundle = await assemble_bundle(primary, preceding=preceding, include_images=include_images)
    drafts = await services.build_inference.infer(
        bundle,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    # Inference may leave the category open; finalization needs one, and a build
    # log bundle without a clearer signal is overwhelmingly a door.
    for draft in drafts:
        draft.category = draft.category or BuildCategory.DOOR
    if not drafts or dry_run:
        return [draft.finalize() for draft in drafts]

    uploader_account_id = await account_id_for(services.accounts, primary[0].author)
    prepared: list[AttachmentLifecycle] = []
    for message in primary:
        for attachment in message.attachments:
            identity = str(attachment.id)
            try:
                classified = classify_attachment(
                    attachment.filename,
                    attachment.content_type,
                    attachment.size,
                    max_bytes=services.schematics.limits.max_upload_bytes,
                )
            except SquidError as error:
                prepared.append(
                    AttachmentLifecycle(
                        identity,
                        attachment.filename,
                        failure=AttachmentFailure("classification", error.public_detail()),
                    )
                )
                continue
            try:
                data = await attachment.read()
            except discord.HTTPException, OSError:
                logger.warning("Could not download inferred attachment %s", attachment.filename, exc_info=True)
                prepared.append(
                    AttachmentLifecycle(
                        identity,
                        attachment.filename,
                        classification=classified,
                        failure=AttachmentFailure("download", "The attachment could not be downloaded."),
                    )
                )
                continue
            if classified.kind == "schematic":
                if not services.schematics.available:
                    prepared.append(
                        AttachmentLifecycle(
                            identity,
                            classified.filename,
                            classification=classified,
                            failure=AttachmentFailure(
                                "analysis", "Schematic analysis is not available on this instance."
                            ),
                        )
                    )
                    continue
                request = IngestRequest(
                    data=data, filename=classified.filename, uploaded_by_account_id=uploader_account_id
                )
                try:
                    analysis = await services.schematics.ingest(request)
                except SquidError as error:
                    logger.warning("Could not analyze inferred schematic %s", classified.filename, exc_info=True)
                    prepared.append(
                        AttachmentLifecycle(
                            identity,
                            classified.filename,
                            classification=classified,
                            request=request,
                            failure=AttachmentFailure("analysis", error.public_detail()),
                        )
                    )
                except Exception:
                    logger.exception("Unexpected failure analyzing inferred schematic %s", classified.filename)
                    prepared.append(
                        AttachmentLifecycle(
                            identity,
                            classified.filename,
                            classification=classified,
                            request=request,
                            failure=AttachmentFailure("analysis", "The schematic could not be analyzed."),
                        )
                    )
                else:
                    prepared.append(
                        AttachmentLifecycle(
                            identity,
                            classified.filename,
                            classification=classified,
                            request=request,
                            analysis=analysis,
                        )
                    )
                continue
            try:
                url = await mirror.upload(classified.filename, data, classified.content_type)
            except (SquidError, discord.HTTPException, OSError) as error:
                logger.warning("Could not mirror inferred attachment %s", attachment.filename, exc_info=True)
                detail = (
                    error.public_detail()
                    if isinstance(error, SquidError)
                    else "The image or video could not be stored."
                )
                prepared.append(
                    AttachmentLifecycle(
                        identity,
                        classified.filename,
                        classification=classified,
                        failure=AttachmentFailure("mirror", detail),
                    )
                )
                continue
            prepared.append(
                AttachmentLifecycle(
                    identity,
                    classified.filename,
                    classification=classified,
                    media_url=url,
                )
            )

    attachments = default_only_usable(prepared)
    duplicate_evidence, duplicate_failures = await _duplicate_evidence(services, attachments)

    builds: list[Build] = []
    for draft in drafts:
        for attachment in attachments:
            if attachment.media_url is not None and attachment.classification is not None:
                draft.add_link(attachment.classification.kind, attachment.media_url)
        build = draft.finalize()
        failures = [*attachment_failure_evidence(attachments), *duplicate_failures]
        if failures:
            build.extra_info["attachment_failures"] = deepcopy(failures)
        if duplicate_evidence:
            build.extra_info["schematic_duplicates"] = deepcopy(duplicate_evidence)

        await services.builds.submit(build, submitter_account_id=uploader_account_id, ai_generated=True)
        assert build.id is not None
        builds.append(build)
        if record_failures := await _record_analyses(services, build, attachments):
            existing = list(build.extra_info.get("attachment_failures", ()))
            build.extra_info["attachment_failures"] = [*existing, *record_failures]
            try:
                await services.builds.save(build)
            except Exception:
                # The build itself is already durable. The caller still receives this object and
                # renders the truthful evidence on its review card even if enrichment persistence
                # needs later operator recovery.
                logger.exception("Could not persist attachment failure evidence for build %s", build.id)

    return builds


async def _duplicate_evidence(
    services: BotServices,
    attachments: tuple[AttachmentLifecycle, ...],
) -> tuple[list[SchematicDuplicateInfo], list[AttachmentFailureInfo]]:
    """Check every usable schematic and retain partial results by attachment identity."""
    matches = []
    failures: list[AttachmentFailureInfo] = []
    for attachment in attachments:
        if attachment.analysis is None:
            continue
        try:
            duplicates = await services.schematics.find_duplicates(attachment.analysis)
        except SquidError as error:
            logger.warning("Could not check inferred schematic %s for duplicates", attachment.filename, exc_info=True)
            failures.append(attachment_failure_for(attachment, "duplicate-check", error.public_detail()))
            continue
        except Exception:
            logger.exception("Unexpected failure checking inferred schematic %s for duplicates", attachment.filename)
            failures.append(
                attachment_failure_for(
                    attachment,
                    "duplicate-check",
                    "This schematic could not be checked for possible duplicates.",
                )
            )
            continue
        matches.extend((attachment, candidate) for candidate in duplicates)

    titles: dict[int, str] = {}
    for build_id in {candidate.build_id for _, candidate in matches}:
        try:
            candidate_build = await services.builds.get(build_id)
        except Exception:
            logger.exception("Could not load duplicate build %s for inferred attachment evidence", build_id)
            continue
        if candidate_build is not None:
            titles[build_id] = candidate_build.title
    return merge_duplicate_evidence(matches, titles), failures


async def _record_analyses(
    services: BotServices,
    build: Build,
    attachments: tuple[AttachmentLifecycle, ...],
) -> list[AttachmentFailureInfo]:
    """Persist each distinct successful analysis without inventing a primary selection."""
    assert build.id is not None
    grouped: dict[str, list[AttachmentLifecycle]] = {}
    for attachment in attachments:
        if attachment.request is not None and attachment.analysis is not None:
            grouped.setdefault(attachment.analysis.sha256, []).append(attachment)

    failures: list[AttachmentFailureInfo] = []
    for sha256 in sorted(grouped):
        group = grouped[sha256]
        attachment = next((item for item in group if item.primary), min(group, key=lambda item: item.identity))
        assert attachment.request is not None and attachment.analysis is not None
        try:
            await services.schematics.record(
                build.id,
                attachment.analysis,
                attachment.request,
                primary=any(item.primary for item in group),
            )
        except SquidError as error:
            logger.warning("Could not record inferred schematic analysis for build %s", build.id, exc_info=True)
            failures.extend(
                attachment_failure_for(item, "record", error.public_detail())
                for item in sorted(group, key=lambda item: item.identity)
            )
        except Exception:
            logger.exception("Unexpected failure recording inferred schematic analysis for build %s", build.id)
            failures.extend(
                attachment_failure_for(
                    item,
                    "record",
                    "The analyzed schematic could not be attached to the saved submission.",
                )
                for item in sorted(group, key=lambda item: item.identity)
            )
    return failures

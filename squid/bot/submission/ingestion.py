"""Shared ingestion of inferred Discord message bundles."""

import logging
from collections.abc import Sequence

import discord

from squid.bot.message_adapter import to_tracked_message
from squid.bot.submission.attachments import classify_attachment
from squid.bot.submission.media import MediaMirror
from squid.bot.submission.message_context import assemble_bundle
from squid.builds.domain import Build, BuildCategory
from squid.core.errors import SquidError
from squid.runtime import BotServices
from squid.schematics.application import IngestedSchematic, IngestRequest

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

    media_urls: dict[str, list[str]] = {"image": [], "video": []}
    pending_schematics: list[tuple[IngestRequest, IngestedSchematic]] = []
    uploader_id = primary[0].author.id
    for message in primary:
        for attachment in message.attachments:
            try:
                classified = classify_attachment(
                    attachment.filename,
                    attachment.content_type,
                    attachment.size,
                    max_bytes=services.schematics.limits.max_upload_bytes,
                )
                data = await attachment.read()
            except (SquidError, discord.HTTPException, OSError):
                logger.warning("Could not read inferred attachment %s", attachment.filename, exc_info=True)
                continue
            if classified.kind == "schematic":
                if not services.schematics.available:
                    continue
                request = IngestRequest(data=data, filename=classified.filename, uploaded_by_discord_id=uploader_id)
                try:
                    pending_schematics.append((request, await services.schematics.ingest(request)))
                except SquidError:
                    logger.warning("Could not analyze inferred schematic %s", classified.filename, exc_info=True)
                continue
            try:
                url = await mirror.upload(classified.filename, data, classified.content_type)
            except OSError:
                logger.warning("Could not mirror inferred attachment %s", attachment.filename, exc_info=True)
                continue
            media_urls[classified.kind].append(url)

    builds: list[Build] = []
    for draft in drafts:
        for url in media_urls["image"]:
            draft.add_link("image", url)
        for url in media_urls["video"]:
            draft.add_link("video", url)
        build = draft.finalize()
        if pending_schematics:
            try:
                duplicates = await services.schematics.find_duplicates(pending_schematics[0][1])
                if duplicates:
                    build.extra_info["schematic_duplicates"] = [
                        {
                            "build_id": candidate.build_id,
                            "tier": candidate.tier,
                            "footprint_distance": candidate.footprint_distance,
                        }
                        for candidate in duplicates
                    ]
            except SquidError:
                logger.warning("Could not check an inferred schematic for duplicates", exc_info=True)

        await services.builds.submit(build, submitter_id=uploader_id, ai_generated=True)
        assert build.id is not None
        builds.append(build)
        for index, (request, ingested) in enumerate(pending_schematics):
            try:
                await services.schematics.record(build.id, ingested, request, primary=index == 0)
            except SquidError:
                logger.warning(
                    "Could not record inferred schematic analysis for build %s",
                    build.id,
                    exc_info=True,
                    extra={
                        "squid.build.id": build.id,
                        "squid.schematic.format": ingested.analysis.metrics.source_format.value,
                    },
                )

    # The tracked-message table is keyed by Discord message id, so a source can identify the
    # bundle for idempotency but cannot point to multiple inferred builds.
    tracked_build = builds[0]
    assert tracked_build.id is not None
    for message in primary:
        await services.messages.track(
            to_tracked_message(message),
            purpose="build_original_message",
            build_id=tracked_build.id,
        )
    return builds

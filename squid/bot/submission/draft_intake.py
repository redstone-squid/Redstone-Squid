"""Discord file downloads handed to durable submission intake."""

import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path
from uuid import UUID, uuid5

import anyio
import discord

from squid.core.errors import ValidationError
from squid.runtime import BotServices
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.application.drafts import DraftActor
from squid.submissions.application.intake import IntakeStatus
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid_ui.forms import UploadedFile

logger = logging.getLogger(__name__)


async def receive_attachments(
    services: BotServices, draft_id: UUID, actor_id: int, attachments: Sequence[discord.Attachment]
) -> None:
    """Reserve every source before downloading so an interrupted bundle stays incomplete."""
    for attachment in attachments:
        await services.submission_intake.reserve(
            draft_id, actor_id, uuid5(draft_id, str(attachment.id)), attachment.filename, attachment.content_type
        )
    for attachment in attachments:
        await receive_attachment(services, draft_id, actor_id, uuid5(draft_id, str(attachment.id)), attachment)


async def receive_attachment(
    services: BotServices, draft_id: UUID, actor_id: int, source_id: UUID, attachment: discord.Attachment
) -> None:
    """Download one bounded source, preserving its failed state for explicit retry."""
    retained = next(item for item in await services.submission_intake.list(draft_id, actor_id) if item.id == source_id)
    if retained.status is IntakeStatus.READY:
        return
    try:
        await _download_and_register(services, draft_id, actor_id, source_id, attachment)
    except Exception:
        await services.submission_intake.fail(draft_id, actor_id, source_id)
        logger.warning("Submission attachment intake failed for source %s", source_id, exc_info=True)


async def _download_and_register(
    services: BotServices, draft_id: UUID, actor_id: int, source_id: UUID, attachment: discord.Attachment
) -> None:
    if attachment.size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
        message = "Discord submission attachments are limited to 16 MiB each."
        raise ValidationError(message)
    with tempfile.TemporaryDirectory(prefix="squid-submission-") as directory:
        path = Path(directory) / "source"
        with anyio.fail_after(60):
            await attachment.save(path)
        if path.stat().st_size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
            message = "Downloaded attachment exceeds the intake limit."
            raise ValidationError(message)
        await services.submission_intake.register_file(draft_id, actor_id, source_id, path, attachment.content_type)


async def retry_uploaded_file(
    services: BotServices, draft_id: UUID, actor_id: int, original_id: UUID, source: UploadedFile
) -> UUID:
    """Retain a replacement attempt and discard the old requirement only after registration."""
    from uuid import uuid4

    new_id = uuid4()
    await services.submission_intake.reserve(draft_id, actor_id, new_id, source.name, source.media_type)
    try:
        await _register_uploaded_file(services, draft_id, actor_id, new_id, source)
    except Exception:
        await services.submission_intake.fail(draft_id, actor_id, new_id)
        raise
    await services.submission_intake.discard(draft_id, actor_id, original_id)
    return new_id


async def _register_uploaded_file(
    services: BotServices, draft_id: UUID, actor_id: int, source_id: UUID, source: UploadedFile
) -> None:
    if source.size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
        message = "Files are limited to 16 MiB."
        raise ValidationError(message)
    with tempfile.TemporaryDirectory(prefix="squid-retry-") as directory:
        path = Path(directory) / "source"
        with anyio.fail_after(60):
            data = await source.read()
        if len(data) > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
            message = "Downloaded file exceeds the intake limit."
            raise ValidationError(message)
        path.write_bytes(data)
        await services.submission_intake.register_file(draft_id, actor_id, source_id, path, source.media_type)


async def receive_retained_file(services: BotServices, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
    """Resolve an explicit assignment using its retained Discord source or preserve failure."""
    attachment = next(item for item in await services.submission_intake.list(draft_id, actor) if item.id == source_id)
    source = attachment.source
    if source is None:
        message = "Upload a replacement for this file."
        raise ValidationError(message)
    if attachment.status in {IntakeStatus.READY, IntakeStatus.DISCARDED}:
        return
    await services.submission_intake.reserve(draft_id, actor, source.id, source.filename, source.content_type)
    try:
        await _download_retained_file(services, draft_id, actor, source)
    except Exception:
        await services.submission_intake.fail(draft_id, actor, source.id)
        raise


async def _download_retained_file(
    services: BotServices, draft_id: UUID, actor: DraftActor, source: SubmissionSourceFile
) -> None:
    from urllib.parse import urlparse

    import aiohttp

    url = urlparse(source.url)
    if url.scheme != "https" or url.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}:
        message = "This retained download URL is not a Discord attachment source."
        raise ValidationError(message)
    if source.size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
        message = "Discord submission attachments are limited to 16 MiB each."
        raise ValidationError(message)
    with tempfile.TemporaryDirectory(prefix="squid-assignment-") as directory:
        path = Path(directory) / "source"
        with anyio.fail_after(60):
            async with aiohttp.ClientSession() as client, client.get(source.url, allow_redirects=False) as response:
                response.raise_for_status()
                size = 0
                with path.open("wb") as output:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > SCHEMATIC_FILE_SCHEMA_MAX_BYTES:
                            message = "Downloaded source exceeds the intake limit."
                            raise ValidationError(message)
                        output.write(chunk)
        await services.submission_intake.register_file(draft_id, actor, source.id, path, source.content_type)

"""Receive Discord files for a retained submission draft.

Sources are reserved as required inputs before downloading begins. These reservations
keep failed or unfinished downloads visible to finalization. Registration hands the
bytes to the shared media or schematic intake service; finalization checks their
processing state before admitting the build.
"""

import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid5

import aiohttp
import anyio
import discord

from squid.core.errors import ValidationError
from squid.runtime import BotServices
from squid.submissions.application.drafts import DraftActor
from squid.submissions.domain.intake import IntakeStatus
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
    """Receive an already reserved source, skipping it if it is ready.

    Download and registration failures are recorded and logged instead of raised,
    allowing the rest of an attachment bundle to continue. Cancellation propagates.
    """
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
    max_bytes = services.submission_intake.max_file_bytes(attachment.filename, attachment.content_type)
    if attachment.size > max_bytes:
        message = f"This attachment exceeds the upload limit of {max_bytes} bytes."
        raise ValidationError(message)
    with tempfile.TemporaryDirectory(prefix="squid-submission-") as directory:
        path = Path(directory) / "source"
        with anyio.fail_after(60):
            await attachment.save(path)
        if path.stat().st_size > max_bytes:
            message = "Downloaded attachment exceeds the intake limit."
            raise ValidationError(message)
        await services.submission_intake.register_file(draft_id, actor_id, source_id, path, attachment.content_type)


async def register_uploaded_file(
    services: BotServices, draft_id: UUID, actor_id: DraftActor, source_id: UUID, source: UploadedFile
) -> None:
    max_bytes = services.submission_intake.max_file_bytes(source.name, source.media_type)
    if source.size > max_bytes:
        message = f"This file exceeds the upload limit of {max_bytes} bytes."
        raise ValidationError(message)
    with tempfile.TemporaryDirectory(prefix="squid-retry-") as directory:
        path = Path(directory) / "source"
        with anyio.fail_after(60):
            data = await source.read()
        if len(data) > max_bytes:
            message = "Downloaded file exceeds the intake limit."
            raise ValidationError(message)
        path.write_bytes(data)
        await services.submission_intake.register_file(draft_id, actor_id, source_id, path, source.media_type)


async def receive_retained_file(services: BotServices, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
    """Download a retained Discord source after the user assigns it to a draft.

    Ready and discarded sources need no work. Download failures are recorded and
    re-raised so the caller can offer replacement or discard controls.
    """
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
    url = urlparse(source.url)
    if url.scheme != "https" or url.hostname not in {"cdn.discordapp.com", "media.discordapp.net"}:
        message = "This retained download URL is not a Discord attachment source."
        raise ValidationError(message)
    max_bytes = services.submission_intake.max_file_bytes(source.filename, source.content_type)
    if source.size > max_bytes:
        message = f"This attachment exceeds the upload limit of {max_bytes} bytes."
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
                        if size > max_bytes:
                            message = "Downloaded source exceeds the intake limit."
                            raise ValidationError(message)
                        output.write(chunk)
        await services.submission_intake.register_file(draft_id, actor, source.id, path, source.content_type)

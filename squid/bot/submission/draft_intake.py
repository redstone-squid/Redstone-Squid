"""Receive Discord files for a retained submission draft.

Sources are reserved as required inputs before downloading begins. These reservations
keep failed or unfinished downloads visible to finalization. Registration hands the
bytes to the shared media or schematic intake service; finalization checks their
processing state before admitting the build.
"""

import logging
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4, uuid5

import aiohttp
import anyio
import discord

from squid.bot.edit_controller import EditController
from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.core.submissions.application.attachment_edits import DRAFT_ATTACHMENTS, AttachmentValues, FileMembership
from squid.core.submissions.application.edits import draft_target
from squid.core.submissions.application.intake import classify_supplied_file
from squid.core.submissions.domain.intake import IntakeStatus
from squid.core.submissions.domain.source_files import SubmissionSourceFile
from squid.runtime import BotServices
from squid_ui.content.text import TextLike
from squid_ui.interactions.forms import UploadedFile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DraftUploads:
    services: BotServices
    draft_id: UUID
    resolve_actor: Callable[[], Awaitable[Actor]]
    edits: EditController

    async def _stage(self, files: Sequence[UploadedFile], revision: int) -> tuple[FileMembership, ...]:
        memberships = tuple(
            FileMembership(
                source_id=uuid4(),
                present=True,
                filename=file.name,
                kind=classify_supplied_file(file.name, file.media_type),
            )
            for file in files
        )
        await self.services.submission_uploads.stage(self.draft_id, await self.resolve_actor(), revision, memberships)
        return memberships

    async def add(self, files: Sequence[UploadedFile], revision: int, label: TextLike) -> int:
        """Keep failed additions visible and return the number that need attention."""
        memberships = await self._stage(files, revision)
        failed = 0
        for item, file in zip(memberships, files, strict=True):
            try:
                await register_uploaded_file(
                    self.services, self.draft_id, await self.resolve_actor(), item.source_id, file
                )
            except Exception:
                await self.services.submission_intake.fail(self.draft_id, await self.resolve_actor(), item.source_id)
                failed += 1
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self.draft_id), revision).save(
            AttachmentValues(files=memberships).edit_values(), label
        )
        return failed

    async def replace(self, original: UUID, file: UploadedFile, revision: int, label: TextLike) -> UUID | None:
        """Swap references in one edit once the replacement bytes are registered."""
        (replacement,) = await self._stage((file,), revision)
        try:
            await register_uploaded_file(
                self.services, self.draft_id, await self.resolve_actor(), replacement.source_id, file
            )
        except Exception:
            return None
        files = (FileMembership(source_id=original, present=False), replacement)
        await self.edits.bind(DRAFT_ATTACHMENTS, draft_target(self.draft_id), revision).save(
            AttachmentValues(files=files).edit_values(), label
        )
        return replacement.source_id


async def receive_attachments(
    services: BotServices, draft_id: UUID, actor: Actor, attachments: Sequence[discord.Attachment]
) -> None:
    """Reserve every source before downloading so an interrupted bundle stays incomplete."""
    for attachment in attachments:
        await services.submission_intake.reserve(
            draft_id, actor, uuid5(draft_id, str(attachment.id)), attachment.filename, attachment.content_type
        )
    for attachment in attachments:
        await receive_attachment(services, draft_id, actor, uuid5(draft_id, str(attachment.id)), attachment)


async def receive_attachment(
    services: BotServices, draft_id: UUID, actor: Actor, source_id: UUID, attachment: discord.Attachment
) -> None:
    """Receive an already reserved source, skipping it if it is ready.

    Download and registration failures are recorded and logged instead of raised,
    allowing the rest of an attachment bundle to continue. Cancellation propagates.
    """
    retained = next(item for item in await services.submission_intake.list(draft_id, actor) if item.id == source_id)
    if retained.status is IntakeStatus.READY:
        return
    try:
        await _download_and_register(services, draft_id, actor, source_id, attachment)
    except Exception:
        await services.submission_intake.fail(draft_id, actor, source_id)
        logger.warning("Submission attachment intake failed for source %s", source_id, exc_info=True)


async def _download_and_register(
    services: BotServices, draft_id: UUID, actor: Actor, source_id: UUID, attachment: discord.Attachment
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
        await services.submission_intake.register_file(draft_id, actor, source_id, path, attachment.content_type)


async def register_uploaded_file(
    services: BotServices, draft_id: UUID, actor: Actor, source_id: UUID, source: UploadedFile
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
        await services.submission_intake.register_file(draft_id, actor, source_id, path, source.media_type)


async def receive_retained_file(services: BotServices, draft_id: UUID, actor: Actor, source_id: UUID) -> None:
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
    services: BotServices, draft_id: UUID, actor: Actor, source: SubmissionSourceFile
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

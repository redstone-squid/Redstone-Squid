"""Reserve every supplied attachment before downloading any of them.

A failed download remains a draft requirement, so finalization cannot silently omit that file.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast, override
from uuid import UUID, uuid4

import aiohttp
import discord
import pytest
import wrapture

from squid.bot.submission.draft_intake import receive_attachments, receive_retained_file, register_uploaded_file
from squid.core.actors import ActingContext, Actor
from squid.core.errors import ValidationError
from squid.core.media.application.jobs import MediaProcessingJobService
from squid.core.media.domain import MediaLimits
from squid.core.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.core.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.submissions.domain.intake import IntakeStatus
from squid.core.submissions.domain.source_files import SubmissionSourceFile
from squid_ui.interactions.forms import UploadedFile
from tests.support.services import bot_services


class Intake(SubmissionAttachmentIntake):
    def __init__(self, *, media_limits: MediaLimits | None = None) -> None:
        self._schematics = cast(DraftSchematicService, SimpleNamespace(max_bytes=SCHEMATIC_FILE_SCHEMA_MAX_BYTES))
        self._media = cast(MediaProcessingJobService, SimpleNamespace(limits=media_limits or MediaLimits()))
        self.files: dict[UUID, SuppliedAttachment] = {}
        self.registered: list[bytes] = []

    @override
    async def reserve(
        self, draft_id: UUID, source_id: UUID, filename: str, content_type: str | None, *, context: ActingContext
    ) -> SuppliedAttachment:
        self.files[source_id] = SuppliedAttachment(source_id, filename, "image", IntakeStatus.PENDING)
        return self.files[source_id]

    @override
    async def list(self, draft_id: UUID, *, context: ActingContext) -> tuple[SuppliedAttachment, ...]:
        return tuple(self.files.values())

    @override
    async def register_file(
        self, draft_id: UUID, source_id: UUID, path: Path, content_type: str | None, *, context: ActingContext
    ) -> None:
        self.registered.append(path.read_bytes())
        self.files[source_id] = replace(self.files[source_id], status=IntakeStatus.READY)

    @override
    async def fail(self, draft_id: UUID, source_id: UUID, *, context: ActingContext) -> None:
        self.files[source_id] = replace(self.files[source_id], status=IntakeStatus.FAILED)


@dataclass
class Attachment:
    id: int
    intake: Intake
    fails: bool = False
    filename: str = "image.png"
    content_type: str = "image/png"
    size: int = 4
    data: bytes = b"file"
    expected_reservations: int = 1

    async def save(self, path: Path) -> None:
        # Reserve both requirements before attempting either download.
        assert len(self.intake.files) == self.expected_reservations
        if self.fails:
            raise OSError("download interrupted")
        path.write_bytes(self.data)


async def test_reserves_all_files_before_download_and_retains_partial_failure() -> None:
    intake = Intake()
    services = bot_services(submission_intake=intake)
    attachments = [
        Attachment(1, intake, fails=True, expected_reservations=2),
        Attachment(2, intake, expected_reservations=2),
    ]
    await receive_attachments(services, uuid4(), Actor(account_id=7), cast(list[discord.Attachment], attachments))
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED, IntakeStatus.READY]
    assert intake.registered == [b"file"]


@pytest.mark.parametrize("size", [500 * 1024 * 1024 + 1, 600 * 1024 * 1024])
async def test_oversized_source_is_retained_without_downloading(size: int) -> None:
    intake = Intake()
    services = bot_services(submission_intake=intake)
    await receive_attachments(
        services, uuid4(), Actor(account_id=7), cast(list[discord.Attachment], [Attachment(1, intake, size=size)])
    )
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED]
    assert intake.registered == []


@pytest.mark.parametrize("filename", ["clip.mp4", "image.png", "build.schem"])
async def test_download_uses_limit_for_file_type(filename: str) -> None:
    intake = Intake()
    data = b"x" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1)
    attachment = Attachment(
        1, intake, filename=filename, content_type="application/octet-stream", size=len(data), data=data
    )
    await receive_attachments(
        bot_services(submission_intake=intake), uuid4(), Actor(account_id=7), [cast(discord.Attachment, attachment)]
    )
    expected = IntakeStatus.FAILED if filename.endswith(".schem") else IntakeStatus.READY
    assert [item.status for item in intake.files.values()] == [expected]
    assert bool(intake.registered) is (expected is IntakeStatus.READY)


@dataclass(frozen=True)
class CDNResponse:
    chunks: tuple[bytes, ...]

    def raise_for_status(self) -> None:
        pass

    @property
    def content(self) -> CDNResponse:
        return self

    async def iter_chunked(self, _size: int) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


@asynccontextmanager
async def cdn_response(*chunks: bytes) -> AsyncIterator[CDNResponse]:
    yield CDNResponse(chunks)


@pytest.mark.parametrize("declared_size", [4, 9])
async def test_retained_download_enforces_configured_limit(declared_size: int) -> None:
    intake = Intake(media_limits=MediaLimits(max_source_bytes=8))
    source = SubmissionSourceFile(
        uuid4(), "clip.mp4", "video/mp4", "https://cdn.discordapp.com/clip.mp4", declared_size
    )
    intake.files[source.id] = SuppliedAttachment(source.id, source.filename, "video", IntakeStatus.PENDING, source)
    get = wrapture.binding(aiohttp.ClientSession, "get").on_call.returns(cdn_response(b"1234", b"56789"))
    with wrapture.timeline(get):
        with pytest.raises(ValidationError, match="limit"):
            await receive_retained_file(bot_services(submission_intake=intake), uuid4(), Actor(account_id=7), source.id)
        get.events.assert_times(int(declared_size <= 8))

    assert intake.files[source.id].status is IntakeStatus.FAILED
    assert intake.registered == []


async def test_retained_video_larger_than_schematic_limit_is_registered() -> None:
    intake = Intake()
    data = b"x" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1)
    source = SubmissionSourceFile(uuid4(), "clip.mp4", "video/mp4", "https://cdn.discordapp.com/clip.mp4", len(data))
    intake.files[source.id] = SuppliedAttachment(source.id, source.filename, "video", IntakeStatus.PENDING, source)
    get = wrapture.binding(aiohttp.ClientSession, "get").on_call.returns(cdn_response(data))
    with wrapture.timeline(get):
        await receive_retained_file(bot_services(submission_intake=intake), uuid4(), Actor(account_id=7), source.id)
        get.events.with_args(url=source.url, allow_redirects=False).assert_once()

    assert intake.files[source.id].status is IntakeStatus.READY
    assert intake.registered == [data]


async def test_uploaded_video_larger_than_schematic_limit_is_registered() -> None:
    intake = Intake()
    draft_id, source_id = uuid4(), uuid4()
    data = b"x" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1)

    async def read() -> bytes:
        return data

    source = UploadedFile("clip.mp4", "video/mp4", len(data), "", read)
    await intake.reserve(
        draft_id, source_id, source.name, source.media_type, context=ActingContext.for_actor(Actor(account_id=7))
    )
    await register_uploaded_file(
        bot_services(submission_intake=intake), draft_id, Actor(account_id=7), source_id, source
    )
    assert intake.registered == [data]

"""Reserve every supplied attachment before downloading any of them.

A failed download remains a draft requirement, so finalization cannot silently omit that file.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast, override
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import discord
import pytest

from squid.bot.submission.draft_intake import receive_attachments, receive_retained_file, register_uploaded_file
from squid.core.actors import Actor
from squid.core.errors import ValidationError
from squid.media.application.jobs import MediaNormalizationJobService
from squid.media.domain import MediaLimits
from squid.schematics.domain.models import SCHEMATIC_FILE_SCHEMA_MAX_BYTES
from squid.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.submissions.application.schematics import DraftSchematicService
from squid.submissions.domain.intake import IntakeStatus
from squid.submissions.domain.source_files import SubmissionSourceFile
from squid_ui.forms import UploadedFile
from tests.support.services import bot_services


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        self._schematics = cast(DraftSchematicService, MagicMock(max_bytes=SCHEMATIC_FILE_SCHEMA_MAX_BYTES))
        self._media = cast(MediaNormalizationJobService, MagicMock(limits=MediaLimits()))
        self.files: dict[UUID, SuppliedAttachment] = {}
        self.registered: list[bytes] = []

    @override
    async def reserve(
        self, draft_id: UUID, actor: Actor, source_id: UUID, filename: str, content_type: str | None
    ) -> SuppliedAttachment:
        self.files[source_id] = SuppliedAttachment(source_id, filename, "image", IntakeStatus.PENDING)
        return self.files[source_id]

    @override
    async def list(self, draft_id: UUID, actor: Actor) -> tuple[SuppliedAttachment, ...]:
        return tuple(self.files.values())

    @override
    async def register_file(
        self, draft_id: UUID, actor: Actor, source_id: UUID, path: Path, content_type: str | None
    ) -> None:
        self.registered.append(path.read_bytes())
        self.files[source_id] = replace(self.files[source_id], status=IntakeStatus.READY)

    @override
    async def fail(self, draft_id: UUID, actor: Actor, source_id: UUID) -> None:
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


@pytest.mark.parametrize("declared_size", [4, 9])
async def test_retained_download_enforces_configured_limit(monkeypatch: pytest.MonkeyPatch, declared_size: int) -> None:
    intake = Intake()
    intake._media = cast(MediaNormalizationJobService, MagicMock(limits=MediaLimits(max_source_bytes=8)))
    source = SubmissionSourceFile(
        uuid4(), "clip.mp4", "video/mp4", "https://cdn.discordapp.com/clip.mp4", declared_size
    )
    intake.files[source.id] = SuppliedAttachment(source.id, source.filename, "video", IntakeStatus.PENDING, source)
    response = MagicMock()
    response.content.iter_chunked.return_value.__aiter__.return_value = [b"1234", b"56789"]
    client = MagicMock()
    client.get.return_value.__aenter__.return_value = response
    session = MagicMock()
    session.return_value.__aenter__.return_value = client
    monkeypatch.setattr("squid.bot.submission.draft_intake.aiohttp.ClientSession", session)

    with pytest.raises(ValidationError, match="limit"):
        await receive_retained_file(bot_services(submission_intake=intake), uuid4(), Actor(account_id=7), source.id)

    assert intake.files[source.id].status is IntakeStatus.FAILED
    assert intake.registered == []
    assert session.called is (declared_size <= 8)


async def test_retained_video_larger_than_schematic_limit_is_registered(monkeypatch: pytest.MonkeyPatch) -> None:
    intake = Intake()
    data = b"x" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1)
    source = SubmissionSourceFile(uuid4(), "clip.mp4", "video/mp4", "https://cdn.discordapp.com/clip.mp4", len(data))
    intake.files[source.id] = SuppliedAttachment(source.id, source.filename, "video", IntakeStatus.PENDING, source)
    response = MagicMock()
    response.content.iter_chunked.return_value.__aiter__.return_value = [data]
    client = MagicMock()
    client.get.return_value.__aenter__.return_value = response
    session = MagicMock()
    session.return_value.__aenter__.return_value = client
    monkeypatch.setattr("squid.bot.submission.draft_intake.aiohttp.ClientSession", session)

    await receive_retained_file(bot_services(submission_intake=intake), uuid4(), Actor(account_id=7), source.id)

    assert intake.files[source.id].status is IntakeStatus.READY
    assert intake.registered == [data]


async def test_uploaded_video_larger_than_schematic_limit_is_registered() -> None:
    intake = Intake()
    draft_id, source_id = uuid4(), uuid4()
    data = b"x" * (SCHEMATIC_FILE_SCHEMA_MAX_BYTES + 1)

    async def read() -> bytes:
        return data

    source = UploadedFile("clip.mp4", "video/mp4", len(data), "", read)
    await intake.reserve(draft_id, Actor(account_id=7), source_id, source.name, source.media_type)
    await register_uploaded_file(
        bot_services(submission_intake=intake), draft_id, Actor(account_id=7), source_id, source
    )
    assert intake.registered == [data]

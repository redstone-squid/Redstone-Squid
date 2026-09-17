"""Transport interruption retains supplied-file requirements before processor registration."""

from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import discord
import pytest

from squid.bot.submission.draft_intake import receive_attachments
from squid.runtime import BotServices
from squid.submissions.application.intake import IntakeStatus, SuppliedAttachment


class Intake:
    def __init__(self) -> None:
        self.files: dict[UUID, SuppliedAttachment] = {}
        self.registered: list[bytes] = []

    async def reserve(self, draft: UUID, actor: int, source: UUID, filename: str, mime: str | None) -> None:
        self.files[source] = SuppliedAttachment(source, filename, "image", IntakeStatus.PENDING)

    async def list(self, draft: UUID, actor: int) -> tuple[SuppliedAttachment, ...]:
        return tuple(self.files.values())

    async def register_file(self, draft: UUID, actor: int, source: UUID, path: Path, mime: str | None) -> None:
        self.registered.append(path.read_bytes())
        self.files[source] = replace(self.files[source], status=IntakeStatus.READY)

    async def fail(self, draft: UUID, actor: int, source: UUID) -> None:
        self.files[source] = replace(self.files[source], status=IntakeStatus.FAILED)


@dataclass
class Attachment:
    id: int
    intake: Intake
    fails: bool = False
    filename: str = "image.png"
    content_type: str = "image/png"
    size: int = 4

    async def save(self, path: Path) -> None:
        assert len(self.intake.files) == 2
        if self.fails:
            raise OSError("download interrupted")
        path.write_bytes(b"file")


async def test_reserves_all_files_before_download_and_retains_partial_failure() -> None:
    intake = Intake()
    services = cast(BotServices, SimpleNamespace(submission_intake=intake))
    attachments = [Attachment(1, intake, fails=True), Attachment(2, intake)]
    await receive_attachments(services, uuid4(), 7, cast(list[discord.Attachment], attachments))
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED, IntakeStatus.READY]
    assert intake.registered == [b"file"]


@pytest.mark.parametrize("size", [16 * 1024 * 1024 + 1, 500 * 1024 * 1024])
async def test_oversized_source_is_retained_without_downloading(size: int) -> None:
    intake = Intake()
    services = cast(BotServices, SimpleNamespace(submission_intake=intake))
    await receive_attachments(services, uuid4(), 7, cast(list[discord.Attachment], [Attachment(1, intake, size=size)]))
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED]
    assert intake.registered == []

"""Reserve every supplied attachment before downloading any of them.

A failed download remains a draft requirement, so finalization cannot silently omit that file.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import cast, override
from uuid import UUID, uuid4

import discord
import pytest

from squid.bot.submission.draft_intake import receive_attachments
from squid.submissions.application.drafts import DraftActor
from squid.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.submissions.domain.intake import IntakeStatus
from tests.support.services import bot_services


class Intake(SubmissionAttachmentIntake):
    def __init__(self) -> None:
        self.files: dict[UUID, SuppliedAttachment] = {}
        self.registered: list[bytes] = []

    @override
    async def reserve(
        self, draft_id: UUID, actor: DraftActor, source_id: UUID, filename: str, content_type: str | None
    ) -> SuppliedAttachment:
        self.files[source_id] = SuppliedAttachment(source_id, filename, "image", IntakeStatus.PENDING)
        return self.files[source_id]

    @override
    async def list(self, draft_id: UUID, actor: DraftActor) -> tuple[SuppliedAttachment, ...]:
        return tuple(self.files.values())

    @override
    async def register_file(
        self, draft_id: UUID, actor: DraftActor, source_id: UUID, path: Path, content_type: str | None
    ) -> None:
        self.registered.append(path.read_bytes())
        self.files[source_id] = replace(self.files[source_id], status=IntakeStatus.READY)

    @override
    async def fail(self, draft_id: UUID, actor: DraftActor, source_id: UUID) -> None:
        self.files[source_id] = replace(self.files[source_id], status=IntakeStatus.FAILED)


@dataclass
class Attachment:
    id: int
    intake: Intake
    fails: bool = False
    filename: str = "image.png"
    content_type: str = "image/png"
    size: int = 4

    async def save(self, path: Path) -> None:
        # Reserve both requirements before attempting either download.
        assert len(self.intake.files) == 2
        if self.fails:
            raise OSError("download interrupted")
        path.write_bytes(b"file")


async def test_reserves_all_files_before_download_and_retains_partial_failure() -> None:
    intake = Intake()
    services = bot_services(submission_intake=intake)
    attachments = [Attachment(1, intake, fails=True), Attachment(2, intake)]
    await receive_attachments(services, uuid4(), 7, cast(list[discord.Attachment], attachments))
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED, IntakeStatus.READY]
    assert intake.registered == [b"file"]


@pytest.mark.parametrize("size", [16 * 1024 * 1024 + 1, 500 * 1024 * 1024])
async def test_oversized_source_is_retained_without_downloading(size: int) -> None:
    intake = Intake()
    services = bot_services(submission_intake=intake)
    await receive_attachments(services, uuid4(), 7, cast(list[discord.Attachment], [Attachment(1, intake, size=size)]))
    assert [item.status for item in intake.files.values()] == [IntakeStatus.FAILED]
    assert intake.registered == []

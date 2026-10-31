"""Supplied-file streaming retains failures and resolves replacements in order."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from starlette.requests import Request

from squid.api.v1.submission_intake import upload_file
from squid.core.errors import ValidationError
from squid.submissions.application.intake import IntakeStatus, SubmissionAttachmentIntake, SuppliedAttachment


class Intake:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.data = b""
        self.source = SuppliedAttachment(uuid4(), "image.png", "image", IntakeStatus.READY)

    async def reserve(self, draft: UUID, actor: int, source: UUID, filename: str, mime: str | None) -> None:
        self.events.append("reserve")

    async def register_file(self, draft: UUID, actor: int, source: UUID, path: Path, mime: str | None) -> None:
        self.data = path.read_bytes()
        self.events.append("register")

    async def discard(self, draft: UUID, actor: int, source: UUID) -> None:
        self.events.append("discard")

    async def fail(self, draft: UUID, actor: int, source: UUID) -> None:
        self.events.append("fail")

    async def list(self, draft: UUID, actor: int) -> tuple[SuppliedAttachment, ...]:
        return (self.source,)


def request(data: bytes) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": data, "more_body": False}

    return Request({"type": "http", "headers": [(b"content-type", b"image/png")]}, receive)


async def test_replacement_registers_bytes_before_discarding_original_requirement() -> None:
    intake = Intake()
    result = await upload_file(
        uuid4(),
        uuid4(),
        request(b"source bytes"),
        7,
        cast(SubmissionAttachmentIntake, intake),
        "image.png",
        replaces=uuid4(),
    )
    assert intake.events == ["reserve", "register", "discard"]
    assert intake.data == b"source bytes"
    assert "source" not in result[0].model_dump()


async def test_oversized_upload_is_retained_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("squid.api.v1.submission_intake.SCHEMATIC_FILE_SCHEMA_MAX_BYTES", 3)
    intake = Intake()
    with pytest.raises(ValidationError):
        await upload_file(
            uuid4(), uuid4(), request(b"too large"), 7, cast(SubmissionAttachmentIntake, intake), "image.png"
        )
    assert intake.events == ["reserve", "fail"]
    assert intake.data == b""

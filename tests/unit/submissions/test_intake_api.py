"""Supplied-file streaming retains failures and resolves replacements in order."""

from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from litestar import Request

from squid.api.v1.submission_intake import upload_file
from squid.core.actors import ActingContext, Actor
from squid.core.errors import ValidationError
from squid.core.submissions.application.intake import SubmissionAttachmentIntake, SuppliedAttachment
from squid.core.submissions.domain.intake import IntakeStatus
from tests.support.litestar import make_request


class Intake:
    max_bytes: int = 500 * 1024 * 1024

    def max_file_bytes(self, filename: str, content_type: str | None) -> int:
        return self.max_bytes

    def __init__(self) -> None:
        self.events: list[str] = []
        self.data = b""
        self.source = SuppliedAttachment(uuid4(), "image.png", "image", IntakeStatus.READY)

    async def reserve(
        self, draft: UUID, source: UUID, filename: str, mime: str | None, *, context: ActingContext
    ) -> None:
        self.events.append("reserve")

    async def register_file(
        self, draft: UUID, source: UUID, path: Path, mime: str | None, *, context: ActingContext
    ) -> None:
        self.data = path.read_bytes()
        self.events.append("register")

    async def discard(self, draft: UUID, source: UUID, *, context: ActingContext) -> None:
        self.events.append("discard")

    async def fail(self, draft: UUID, source: UUID, *, context: ActingContext) -> None:
        self.events.append("fail")

    async def list(self, draft: UUID, *, context: ActingContext) -> tuple[SuppliedAttachment, ...]:
        return (self.source,)


def request(data: bytes) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": data, "more_body": False}

    return make_request(
        {"type": "http", "route_handler": upload_file, "headers": [(b"content-type", b"image/png")]}, receive
    )


async def test_replacement_registers_bytes_before_discarding_original_requirement() -> None:
    intake = Intake()
    result = await upload_file.fn(
        uuid4(),
        uuid4(),
        request(b"source bytes"),
        ActingContext.direct(Actor(account_id=7)),
        cast(SubmissionAttachmentIntake, intake),
        "image.png",
        replaces=uuid4(),
    )
    assert intake.events == ["reserve", "register", "discard"]
    assert intake.data == b"source bytes"
    assert result == [intake.source]


async def test_oversized_upload_is_retained_as_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Intake, "max_bytes", 3)
    intake = Intake()
    with pytest.raises(ValidationError):
        await upload_file.fn(
            uuid4(),
            uuid4(),
            request(b"too large"),
            ActingContext.direct(Actor(account_id=7)),
            cast(SubmissionAttachmentIntake, intake),
            "image.png",
        )
    assert intake.events == ["reserve", "fail"]
    assert intake.data == b""

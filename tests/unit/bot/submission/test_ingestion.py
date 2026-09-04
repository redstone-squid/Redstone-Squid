"""Discord bundle ingestion boundary tests."""

from dataclasses import dataclass
from typing import Any, cast

import discord
import pytest

from squid.accounts.application import AccountService
from squid.accounts.domain import Account, AccountConsent, IdentityProvider
from squid.bot.submission import ingestion
from squid.builds.application import BuildInferenceService, BuildService
from squid.builds.domain import Build, BuildCategory, BuildDraft
from squid.runtime import BotServices
from squid.schematics.application import IngestedSchematic
from squid.schematics.application.services import IngestRequest, SchematicService
from squid.schematics.domain import SchematicLimits


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str = "door.litematic"
    content_type: str = "application/octet-stream"
    size: int = 128

    async def read(self) -> bytes:
        return b"schematic-bytes"


@dataclass(frozen=True, slots=True)
class Author:
    id: int = 200


@dataclass(frozen=True, slots=True)
class Message:
    id: int = 100
    attachments: tuple[Attachment, ...] = (Attachment(),)
    author: Author = Author()
    guild: object = object()
    channel: object = object()
    content: str = "submission"


class InferenceService(BuildInferenceService):
    def __init__(self, draft: BuildDraft) -> None:
        self.draft = draft

    async def infer(
        self,
        source: object,
        *,
        model: str,
        reasoning_effort: str | None = None,
    ) -> list[BuildDraft]:
        return [self.draft]


class AccountServiceFake(AccountService):
    def __init__(self) -> None:
        pass

    async def get_or_create_identity(
        self,
        provider: IdentityProvider,
        subject: str,
        *,
        consent: AccountConsent | None = None,
    ) -> Account:
        return Account(id=7)


class BuildServiceRecorder(BuildService):
    def __init__(self) -> None:
        self.submitted: list[Build] = []

    async def submit(self, build: Build, *, submitter_account_id: int, ai_generated: bool) -> Build:
        build.id = 42
        self.submitted.append(build)
        return build


class SchematicServiceRecorder(SchematicService):
    def __init__(self) -> None:
        self.recorded: list[tuple[int, IngestRequest, bool]] = []
        self.ingested = cast(IngestedSchematic, object())

    @property
    def available(self) -> bool:
        return True

    @property
    def limits(self) -> SchematicLimits:
        return SchematicLimits(max_upload_bytes=1024)

    async def ingest(self, request: IngestRequest) -> IngestedSchematic:
        return self.ingested

    async def find_duplicates(
        self,
        ingested: IngestedSchematic,
        *,
        exclude_build_id: int | None = None,
    ) -> list:
        return []

    async def record(
        self,
        build_id: int,
        ingested: IngestedSchematic,
        request: IngestRequest,
        *,
        primary: bool = True,
        publication: object | None = None,
    ) -> int:
        self.recorded.append((build_id, request, primary))
        return 1


class MirrorRecorder:
    def __init__(self) -> None:
        self.uploaded: list[str] = []

    async def upload(self, filename: str, data: bytes, content_type: str | None) -> str:
        self.uploaded.append(filename)
        return f"https://media.example/{filename}"


def service_graph(
    inference_service: BuildInferenceService,
    builds: BuildService,
    schematics: SchematicService,
) -> BotServices:
    unused = cast(Any, object())
    return BotServices(
        builds=builds,
        error_reports=unused,
        build_inference=inference_service,
        restrictions=unused,
        build_queries=unused,
        messages=unused,
        posts=unused,
        permissions=unused,
        permission_admin=unused,
        permission_epoch=unused,
        records=unused,
        record_computation=unused,
        schematics=schematics,
        search=unused,
        tags=unused,
        settings=unused,
        starboards=unused,
        suggestions=unused,
        accounts=AccountServiceFake(),
        versions=unused,
        votes=unused,
        discord_reconciliation=unused,
        domain_events=unused,
        notifications=unused,
        redstoner=unused,
        welcome_relay=unused,
    )


async def test_raw_schematic_is_recorded_privately_without_public_mirroring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = BuildDraft(category=BuildCategory.DOOR)
    schematics = SchematicServiceRecorder()
    services = service_graph(InferenceService(draft), BuildServiceRecorder(), schematics)
    mirror = MirrorRecorder()

    async def assemble(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(ingestion, "assemble_bundle", assemble)

    result = await ingestion.ingest_message_bundle(
        [cast(discord.Message, Message())],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=mirror,
    )

    (build,) = result
    assert build.id == 42
    assert build.schematic_urls == ()
    assert mirror.uploaded == []
    assert len(schematics.recorded) == 1
    _, request, _ = schematics.recorded[0]
    assert request.uploaded_by_account_id == 7

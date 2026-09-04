"""Discord bundle ingestion boundary tests."""

from copy import deepcopy
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
from squid.schematics.application import DuplicateCandidate, IngestedSchematic
from squid.schematics.application.services import IngestRequest, SchematicService
from squid.schematics.domain import SchematicLimits
from squid.schematics.errors import InvalidSchematicError
from tests.unit.schematics.fakes import make_analysis


@dataclass(frozen=True, slots=True)
class Attachment:
    id: int = 1
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
        self.saved_extra_info: list[dict[str, object]] = []
        self.candidates: dict[int, object] = {}
        self.save_failure: Exception | None = None

    async def submit(self, build: Build, *, submitter_account_id: int, ai_generated: bool) -> Build:
        build.id = 42
        self.submitted.append(build)
        return build

    async def save(self, build: Build) -> Build:
        if self.save_failure is not None:
            raise self.save_failure
        self.saved_extra_info.append(deepcopy(cast(dict[str, object], build.extra_info)))
        return build

    async def get(self, build_id: int) -> Any:
        return self.candidates.get(build_id)


@dataclass(frozen=True, slots=True)
class CandidateBuild:
    title: str


class SchematicServiceRecorder(SchematicService):
    def __init__(self) -> None:
        self.recorded: list[tuple[int, IngestRequest, bool]] = []
        self.ingested_by_filename: dict[str, IngestedSchematic] = {}
        self.ingest_failures: dict[str, Exception] = {}
        self.duplicates: dict[str, list[DuplicateCandidate]] = {}
        self.duplicate_calls: list[str] = []
        self.duplicate_failures: dict[str, Exception] = {}
        self.record_failures: dict[str, Exception] = {}

    @property
    def available(self) -> bool:
        return True

    @property
    def limits(self) -> SchematicLimits:
        return SchematicLimits(max_upload_bytes=1024)

    async def ingest(self, request: IngestRequest) -> IngestedSchematic:
        if failure := self.ingest_failures.get(request.filename):
            raise failure
        return self.ingested_by_filename.setdefault(
            request.filename,
            IngestedSchematic(request.filename[0] * 64, make_analysis()),
        )

    async def find_duplicates(
        self,
        ingested: IngestedSchematic,
        *,
        exclude_build_id: int | None = None,
    ) -> list:
        self.duplicate_calls.append(ingested.sha256)
        if failure := self.duplicate_failures.get(ingested.sha256):
            raise failure
        return self.duplicates.get(ingested.sha256, [])

    async def record(
        self,
        build_id: int,
        ingested: IngestedSchematic,
        request: IngestRequest,
        *,
        primary: bool = True,
        publication: object | None = None,
    ) -> int:
        if failure := self.record_failures.get(ingested.sha256):
            raise failure
        self.recorded.append((build_id, request, primary))
        return 1


class MirrorRecorder:
    def __init__(self) -> None:
        self.uploaded: list[str] = []
        self.failures: dict[str, Exception] = {}

    async def upload(self, filename: str, data: bytes, content_type: str | None) -> str:
        if failure := self.failures.get(filename):
            raise failure
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
    _, request, primary = schematics.recorded[0]
    assert request.uploaded_by_account_id == 7
    assert primary is True


@pytest.mark.parametrize("reverse", [False, True])
async def test_multiple_inferred_schematics_are_order_independent_and_all_checked(
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
) -> None:
    first = Attachment(10, "alpha.litematic")
    second = Attachment(20, "beta.litematic")
    ordered = (second, first) if reverse else (first, second)
    draft = BuildDraft(category=BuildCategory.DOOR)
    builds = BuildServiceRecorder()
    builds.candidates[9] = CandidateBuild("Existing door")
    schematics = SchematicServiceRecorder()
    schematics.duplicates = {
        "a" * 64: [DuplicateCandidate(9, 90, "near", 0.25)],
        "b" * 64: [DuplicateCandidate(9, 91, "identical", 0.0)],
    }
    services = service_graph(InferenceService(draft), builds, schematics)

    async def assemble(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(ingestion, "assemble_bundle", assemble)

    (build,) = await ingestion.ingest_message_bundle(
        [cast(discord.Message, Message(attachments=ordered))],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=MirrorRecorder(),
    )

    assert sorted((request.filename, primary) for _, request, primary in schematics.recorded) == [
        ("alpha.litematic", False),
        ("beta.litematic", False),
    ]
    assert set(schematics.duplicate_calls) == {"a" * 64, "b" * 64}
    assert build.extra_info["schematic_duplicates"] == [
        {
            "build_id": 9,
            "title": "Existing door",
            "tier": "identical",
            "footprint_distance": 0.0,
            "source_attachments": [
                {"attachment_id": "10", "filename": "alpha.litematic"},
                {"attachment_id": "20", "filename": "beta.litematic"},
            ],
        }
    ]


async def test_identical_inferred_schematics_coalesce_without_inventing_a_primary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = BuildDraft(category=BuildCategory.DOOR)
    schematics = SchematicServiceRecorder()
    shared = IngestedSchematic("f" * 64, make_analysis())
    schematics.ingested_by_filename = {"z-copy.litematic": shared, "a-copy.litematic": shared}
    services = service_graph(InferenceService(draft), BuildServiceRecorder(), schematics)

    async def assemble(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(ingestion, "assemble_bundle", assemble)

    await ingestion.ingest_message_bundle(
        [
            cast(
                discord.Message,
                Message(attachments=(Attachment(20, "z-copy.litematic"), Attachment(10, "a-copy.litematic"))),
            )
        ],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=MirrorRecorder(),
    )

    assert [(request.filename, primary) for _, request, primary in schematics.recorded] == [("a-copy.litematic", False)]


async def test_inferred_partial_failures_are_persisted_per_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = BuildDraft(category=BuildCategory.DOOR)
    builds = BuildServiceRecorder()
    schematics = SchematicServiceRecorder()
    schematics.ingest_failures["broken.litematic"] = InvalidSchematicError("Broken schematic.")
    schematics.duplicate_failures["g" * 64] = InvalidSchematicError("Duplicate lookup unavailable.")
    schematics.ingested_by_filename["good.litematic"] = IngestedSchematic("g" * 64, make_analysis())
    services = service_graph(InferenceService(draft), builds, schematics)
    mirror = MirrorRecorder()
    mirror.failures["preview.png"] = InvalidSchematicError("Media storage unavailable.")

    async def assemble(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(ingestion, "assemble_bundle", assemble)

    (build,) = await ingestion.ingest_message_bundle(
        [
            cast(
                discord.Message,
                Message(
                    attachments=(
                        Attachment(1, "broken.litematic"),
                        Attachment(2, "good.litematic"),
                        Attachment(3, "preview.png", "image/png"),
                    )
                ),
            )
        ],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=mirror,
    )

    assert [(failure["filename"], failure["stage"]) for failure in build.extra_info["attachment_failures"]] == [
        ("broken.litematic", "analysis"),
        ("preview.png", "mirror"),
        ("good.litematic", "duplicate-check"),
    ]
    assert builds.submitted[0].extra_info == build.extra_info
    assert [(request.filename, primary) for _, request, primary in schematics.recorded] == [("good.litematic", True)]


async def test_inferred_record_failure_is_saved_and_remains_visible_when_recovery_save_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = BuildDraft(category=BuildCategory.DOOR)
    builds = BuildServiceRecorder()
    schematics = SchematicServiceRecorder()
    schematics.record_failures["d" * 64] = InvalidSchematicError("Analysis row unavailable.")
    services = service_graph(InferenceService(draft), builds, schematics)

    async def assemble(*_args: object, **_kwargs: object) -> object:
        return object()

    monkeypatch.setattr(ingestion, "assemble_bundle", assemble)

    (saved,) = await ingestion.ingest_message_bundle(
        [cast(discord.Message, Message())],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=MirrorRecorder(),
    )

    assert saved.extra_info["attachment_failures"][0]["stage"] == "record"
    assert builds.saved_extra_info[-1] == saved.extra_info

    builds.save_failure = RuntimeError("recovery write unavailable")
    resumed = await ingestion.ingest_message_bundle(
        [cast(discord.Message, Message())],
        [],
        cast(BotServices, services),
        model="test-model",
        mirror=MirrorRecorder(),
    )
    assert resumed[0].extra_info["attachment_failures"][0]["stage"] == "record"

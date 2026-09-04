"""Slash-submission attachment orchestration tests."""

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, cast

import discord
import pytest

import squid_ui as sl
from squid.bot.submission.attachment_enrichment import (
    AttachmentLifecycle,
    default_only_usable,
    select_primary,
)
from squid.bot.submission.attachments import ClassifiedAttachment
from squid.bot.submission.submit import BuildSubmitCommands
from squid.bot.submission.ui.views import SubmissionDeliveryError
from squid.builds.application import BuildService
from squid.builds.domain import Build, BuildCategory, BuildDraft, DoorBuild
from squid.schematics.application import DuplicateCandidate, IngestedSchematic, IngestRequest
from squid.schematics.domain import SchematicLimits
from squid.schematics.errors import InvalidSchematicError
from tests.unit.schematics.fakes import make_analysis


@dataclass(frozen=True, slots=True)
class Attachment:
    id: int
    filename: str
    content_type: str | None
    data: bytes = b"data"
    size: int = 4

    async def read(self) -> bytes:
        return self.data


class Schematics:
    def __init__(self) -> None:
        self.duplicates: dict[str, list[DuplicateCandidate]] = {}
        self.duplicate_calls: list[str] = []
        self.records: list[tuple[str, bool]] = []
        self.duplicate_failures: dict[str, Exception] = {}
        self.record_failures: dict[str, Exception] = {}

    @property
    def available(self) -> bool:
        return True

    @property
    def limits(self) -> SchematicLimits:
        return SchematicLimits(max_upload_bytes=1_024)

    async def ingest(self, request: IngestRequest) -> IngestedSchematic:
        if request.filename.startswith("broken"):
            raise InvalidSchematicError("The schematic contents are invalid.")
        return IngestedSchematic(request.filename[0] * 64, make_analysis())

    async def find_duplicates(self, ingested: IngestedSchematic) -> list[DuplicateCandidate]:
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
    ) -> int:
        assert build_id == 42
        if failure := self.record_failures.get(ingested.sha256):
            raise failure
        self.records.append((request.filename, primary))
        return len(self.records)


class Catbox:
    async def upload(self, filename: str, data: bytes, content_type: str) -> str:
        del data, content_type
        return f"https://files.example/{filename}"


@dataclass(frozen=True, slots=True)
class CandidateBuild:
    title: str


class Builds(BuildService):
    def __init__(self) -> None:
        self.submitted: Build | None = None
        self.saved_extra_info: list[dict[str, object]] = []
        self.save_failure: Exception | None = None

    async def get(self, build_id: int) -> Any:
        return CandidateBuild(f"Candidate {build_id}")

    async def submit(self, build: Build, *, submitter_account_id: int, ai_generated: bool) -> Build:
        del ai_generated
        build.id = 42
        build.submitter_account_id = submitter_account_id
        self.submitted = build
        return build

    async def save(self, build: Build) -> Build:
        if self.save_failure is not None:
            raise self.save_failure
        self.saved_extra_info.append(deepcopy(cast(dict[str, object], build.extra_info)))
        return build


class Handler:
    def __init__(self) -> None:
        self.rendered_extra_info: dict[str, object] | None = None

    async def render_node(self) -> sl.LayoutNode[Any]:
        return sl.status("submitted")

    async def post_for_voting(self) -> None:
        pass


@dataclass(frozen=True, slots=True)
class Services:
    schematics: Schematics


@dataclass(frozen=True, slots=True)
class Bot:
    services: Services
    catbox: Catbox
    handler: Handler

    def for_build(self, build: Build) -> Handler:
        self.handler.rendered_extra_info = deepcopy(cast(dict[str, object], build.extra_info))
        return self.handler


def cog(schematics: Schematics) -> BuildSubmitCommands[Any]:
    instance = BuildSubmitCommands.__new__(BuildSubmitCommands)
    instance.bot = cast(Any, Bot(Services(schematics), Catbox(), Handler()))
    instance.builds = Builds()
    return instance


def lifecycle(
    identity: str,
    filename: str,
    dimensions: tuple[int, int, int],
    *,
    sha256: str | None = None,
) -> AttachmentLifecycle:
    request = IngestRequest(data=identity.encode(), filename=filename)
    return AttachmentLifecycle(
        identity,
        filename,
        classification=ClassifiedAttachment("schematic", filename, "application/octet-stream"),
        request=request,
        analysis=IngestedSchematic(sha256 or identity * 64, make_analysis(dimensions=dimensions)),
    )


def draft() -> BuildDraft:
    return BuildDraft(
        category=BuildCategory.DOOR,
        door_orientation="Door",
        door_width=2,
        door_height=2,
        patterns=["Regular"],
    )


async def test_media_survives_a_sibling_schematic_analysis_failure() -> None:
    commands = cog(Schematics())

    image = await commands._prepare_attachment(
        cast(discord.Attachment, Attachment(1, "image.png", "image/png")), uploader_account_id=7
    )
    broken = await commands._prepare_attachment(
        cast(discord.Attachment, Attachment(2, "broken.litematic", None)), uploader_account_id=7
    )

    assert image.media_url == "https://files.example/image.png"
    assert image.failure is None
    assert broken.analysis is None
    assert broken.failure is not None
    assert broken.failure.stage == "analysis"


async def test_every_analysis_is_duplicate_checked_and_merged_with_build_titles() -> None:
    schematics = Schematics()
    schematics.duplicates = {
        "a" * 64: [DuplicateCandidate(7, 70, "near", 0.5)],
        "b" * 64: [
            DuplicateCandidate(7, 71, "identical", 0.0),
            DuplicateCandidate(8, 80, "structural-match", 0.0),
        ],
    }
    commands = cog(schematics)
    attachments = (lifecycle("a", "first.litematic", (3, 4, 5)), lifecycle("b", "second.litematic", (7, 8, 9)))
    build = DoorBuild(id=42)

    await commands._note_schematic_duplicates(build, attachments)

    assert schematics.duplicate_calls == ["a" * 64, "b" * 64]
    evidence = build.extra_info["schematic_duplicates"]
    assert [(item["build_id"], item["title"], item["tier"]) for item in evidence] == [
        (7, "Candidate 7", "identical"),
        (8, "Candidate 8", "structural-match"),
    ]
    assert {source["filename"] for source in evidence[0]["source_attachments"]} == {
        "first.litematic",
        "second.litematic",
    }


async def test_record_and_mismatch_follow_selected_identity_not_upload_position() -> None:
    schematics = Schematics()
    commands = cog(schematics)
    first = lifecycle("a", "first.litematic", (3, 4, 5))
    second = lifecycle("b", "second.litematic", (7, 8, 9))
    attachments = select_primary(default_only_usable((second, first)), "a")
    build = DoorBuild(id=42, width=10, height=11, depth=12)

    commands._note_dimension_mismatch(build, attachments)
    await commands._record_analyses(build, attachments)

    assert "schematic measures 3x4x5" in build.extra_info["schematic_dimension_mismatch"]
    assert schematics.records == [("first.litematic", True), ("second.litematic", False)]


@pytest.mark.parametrize("reverse", [False, True])
async def test_identical_files_record_once_with_the_selected_name_and_primary_state(reverse: bool) -> None:
    schematics = Schematics()
    commands = cog(schematics)
    selected = lifecycle("a", "chosen.litematic", (3, 4, 5), sha256="f" * 64)
    sibling = lifecycle("b", "copy.litematic", (3, 4, 5), sha256="f" * 64)
    ordered = (sibling, selected) if reverse else (selected, sibling)
    attachments = select_primary(ordered, "a")

    failures = await commands._record_analyses(DoorBuild(id=42), attachments)

    assert failures == []
    assert schematics.records == [("chosen.litematic", True)]


async def test_identical_file_record_failure_retains_every_attachment_identity() -> None:
    schematics = Schematics()
    failure = InvalidSchematicError("The analysis row could not be stored.")
    schematics.record_failures["f" * 64] = failure
    commands = cog(schematics)
    attachments = select_primary(
        (
            lifecycle("b", "copy.litematic", (3, 4, 5), sha256="f" * 64),
            lifecycle("a", "chosen.litematic", (3, 4, 5), sha256="f" * 64),
        ),
        "a",
    )

    failures = await commands._record_analyses(DoorBuild(id=42), attachments)

    assert [(item["attachment_id"], item["filename"]) for item in failures] == [
        ("a", "chosen.litematic"),
        ("b", "copy.litematic"),
    ]


async def test_duplicate_check_failure_is_retained_per_file_without_hiding_sibling_matches() -> None:
    schematics = Schematics()
    failure = InvalidSchematicError("Duplicate lookup is unavailable.")
    schematics.duplicate_failures["a" * 64] = failure
    schematics.duplicates["b" * 64] = [DuplicateCandidate(7, 70, "identical", 0.0)]
    commands = cog(schematics)
    attachments = (lifecycle("a", "unchecked.litematic", (3, 4, 5)), lifecycle("b", "match.litematic", (3, 4, 5)))
    build = DoorBuild(id=42)

    await commands._note_schematic_duplicates(build, attachments)

    assert build.extra_info["attachment_failures"] == [
        {
            "attachment_id": "a",
            "filename": "unchecked.litematic",
            "stage": "duplicate-check",
            "detail": failure.public_detail(),
        }
    ]
    assert build.extra_info["schematic_duplicates"][0]["build_id"] == 7


async def test_record_failures_are_persisted_and_visible_before_delivery() -> None:
    schematics = Schematics()
    failure = InvalidSchematicError("The analysis row could not be stored.")
    schematics.record_failures["a" * 64] = failure
    commands = cog(schematics)
    attachments = default_only_usable((lifecycle("a", "door.litematic", (3, 4, 5)),))

    outcome = await commands._persist_draft(draft(), attachments, uploader_account_id=7)
    builds = cast(Builds, commands.builds)

    expected = {
        "attachment_id": "a",
        "filename": "door.litematic",
        "stage": "record",
        "detail": failure.public_detail(),
    }
    assert outcome.build.extra_info["attachment_failures"] == [expected]
    assert builds.saved_extra_info[-1]["attachment_failures"] == [expected]
    assert cast(Bot, commands.bot).handler.rendered_extra_info == {"attachment_failures": [expected]}


async def test_unexpected_post_save_failure_reports_a_recoverable_saved_outcome() -> None:
    schematics = Schematics()
    schematics.record_failures["a" * 64] = RuntimeError("database connection disappeared")
    commands = cog(schematics)
    builds = cast(Builds, commands.builds)
    builds.save_failure = RuntimeError("could not persist enrichment evidence")
    attachments = default_only_usable((lifecycle("a", "door.litematic", (3, 4, 5)),))

    with pytest.raises(SubmissionDeliveryError) as raised:
        await commands._persist_draft(draft(), attachments, uploader_account_id=7)

    assert builds.submitted is raised.value.outcome.build
    assert raised.value.outcome.delivery_complete is False
    assert "Submission saved" in str(raised.value.outcome.node)


async def test_all_failed_analyses_still_submit_the_build_with_failure_evidence() -> None:
    commands = cog(Schematics())
    failed = await commands._prepare_attachment(
        cast(discord.Attachment, Attachment(9, "broken.litematic", None)), uploader_account_id=7
    )
    attachments = default_only_usable((failed,))
    pending = draft()
    commands._note_attachment_failures(pending, attachments)

    outcome = await commands._persist_draft(pending, attachments, uploader_account_id=7)

    assert outcome.build.id == 42
    assert outcome.build.extra_info["attachment_failures"][0]["stage"] == "analysis"
    assert commands.bot.services.schematics.records == []

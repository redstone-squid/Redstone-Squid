"""Slash-submission attachment orchestration tests."""

from dataclasses import dataclass
from typing import Any, cast

import discord

from squid.bot.submission.attachment_enrichment import (
    AttachmentLifecycle,
    default_only_usable,
    select_primary,
)
from squid.bot.submission.attachments import ClassifiedAttachment
from squid.bot.submission.submit import BuildSubmitCommands
from squid.builds.application import BuildService
from squid.builds.domain import DoorBuild
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
        pass

    async def get(self, build_id: int) -> Any:
        return CandidateBuild(f"Candidate {build_id}")


@dataclass(frozen=True, slots=True)
class Services:
    schematics: Schematics


@dataclass(frozen=True, slots=True)
class Bot:
    services: Services
    catbox: Catbox


def cog(schematics: Schematics) -> BuildSubmitCommands[Any]:
    instance = BuildSubmitCommands.__new__(BuildSubmitCommands)
    instance.bot = cast(Any, Bot(Services(schematics), Catbox()))
    instance.builds = Builds()
    return instance


def lifecycle(identity: str, filename: str, dimensions: tuple[int, int, int]) -> AttachmentLifecycle:
    request = IngestRequest(data=identity.encode(), filename=filename)
    return AttachmentLifecycle(
        identity,
        filename,
        classification=ClassifiedAttachment("schematic", filename, "application/octet-stream"),
        request=request,
        analysis=IngestedSchematic(identity * 64, make_analysis(dimensions=dimensions)),
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
    assert schematics.records == [("second.litematic", False), ("first.litematic", True)]

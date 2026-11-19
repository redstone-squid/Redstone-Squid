"""Translation from normalized submissions into account-keyed builds."""

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from squid.core.builds.domain import BuildCategory, BuildDraft, ShowcaseTag, Status
from squid.core.builds.domain.credits import Credit, CreditAttribution, CreditKind, CreditRole, name_credits
from squid.core.builds.domain.taxonomy import RestrictionField
from squid.core.builds.domain.titles import format_build_display_title
from squid.core.builds.errors import InvalidBuildError
from squid.core.builds.models import Build, DoorBuild, ExtenderBuild
from squid.core.errors import DataIntegrityError, JSONValue, ValidationError
from squid.core.submissions.application.build_target import SubmissionBuildPreparation, preview_build
from squid.core.submissions.application.forms import build_submission_manifest
from squid.core.submissions.application.prefill import project_submission
from squid.core.submissions.domain import (
    BuildSubmissionRejected,
    ChoiceOption,
    DoorOrientation,
    DoorSubmissionDetails,
    DoorTiming,
    DraftSnapshot,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    ExtenderTiming,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionArtifactReadiness,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.core.submissions.domain.links import SubmissionLink
from squid.core.submissions.domain.normalization import normalize_submission
from squid.core.submissions.infrastructure.finalization_payloads import decode_submission, encode_submission
from squid.core.tags.domain import TagAuthority, TagModerationStatus, TagSemanticKind, TagValueType
from squid.core.tags.models import TagDefinition
from squid.core.versions.domain import MinecraftVersion
from squid.core.versions.errors import InvalidVersionError
from squid.sponsors import PublicSponsor
from tests.support.expansion import expansion_document

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000501")
MEDIA_ID = UUID("00000000-0000-4000-8000-000000000502")
SCHEMATIC_ID = UUID("00000000-0000-4000-8000-000000000503")
INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000504")


class FakeBuilds:
    """Prepare build fields without persisting; tests seed created entries to model a completed write."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Build, dict[str, object]]] = []
        self.created: dict[UUID, Build] = {}

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        return self.created.get(draft_id)

    async def prepare_for_account(
        self,
        build: Build,
        *,
        submitter_account_id: int,
        source_submission_draft_id: UUID,
        display_name: str | None,
        ai_generated: bool,
    ) -> Build:
        if self.error is not None:
            raise self.error
        self.calls.append(
            (
                build,
                {
                    "submitter_account_id": submitter_account_id,
                    "source_submission_draft_id": source_submission_draft_id,
                    "display_name": display_name,
                    "ai_generated": ai_generated,
                },
            )
        )
        build.submitter_account_id = submitter_account_id
        build.source_submission_draft_id = source_submission_draft_id
        build.display_name = display_name
        build.ai_generated = ai_generated
        return build


class FakeTags:
    def __init__(self, definitions: tuple[TagDefinition, ...] = ()) -> None:
        self.definitions = definitions

    async def public_definitions(self) -> tuple[TagDefinition, ...]:
        return self.definitions


class FakeVersions:
    def __init__(self, versions: tuple[MinecraftVersion, ...] | None = None) -> None:
        self.versions = versions or (MinecraftVersion("Java", 26, 1, 2),)

    async def list_all(self) -> tuple[MinecraftVersion, ...]:
        return self.versions

    async def resolve_spec(self, version_spec: str) -> list[str]:
        raise ValueError("No compatibility resolution configured")


def _definition(
    stable_key: str,
    kind: TagSemanticKind,
    *,
    restriction_type: str | None = None,
    value_type: TagValueType = TagValueType.NONE,
) -> TagDefinition:
    return TagDefinition(
        id=len(stable_key),
        stable_key=stable_key,
        display_name=stable_key.replace("_", " ").title(),
        authority=TagAuthority.OFFICIAL,
        semantic_kind=kind,
        restriction_type=restriction_type,
        value_type=value_type,
        moderation_status=TagModerationStatus.APPROVED,
    )


def _submission(category: SubmissionCategory) -> NormalizedSubmission:
    details: DoorSubmissionDetails | ExtenderSubmissionDetails | GeneralSubmissionDetails
    if category is SubmissionCategory.DOOR:
        details = DoorSubmissionDetails(SubmissionDimensions(2, 3, 1), DoorOrientation.DOOR)
    elif category is SubmissionCategory.EXTENDER:
        details = ExtenderSubmissionDetails(ExtenderOrientation.HORIZONTAL, 2)
    else:
        details = GeneralSubmissionDetails()
    return NormalizedSubmission(
        source_draft_id=DRAFT_ID,
        owner_account_id=17,
        origin=SubmissionOrigin.WEB,
        schema_id="redstone_squid.submission",
        schema_revision=2,
        category=category,
        display_name="Workshop prototype",
        description="A synchronized submission",
        credits=tuple(name_credits(["Builder"], legacy=True)),
        capture_dimensions=SubmissionDimensions(8, 9, 10),
        source_version="Java 26.1.2",
        version_compatibility=">=26.1",
        taxonomy=SubmissionTaxonomy(),
        schematic_policy=SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility.REVIEWER_ONLY,
            license=None,
            rights_attested=False,
        ),
        completion="August 2026",
        ai_generated=False,
        sponsor_attribution=False,
        artifacts=VerifiedSubmissionArtifacts(),
        details=details,
    )


@pytest.mark.parametrize(
    ("submission_category", "build_category"),
    [
        (SubmissionCategory.DOOR, BuildCategory.DOOR),
        (SubmissionCategory.EXTENDER, BuildCategory.EXTENDER),
        (SubmissionCategory.UTILITY, BuildCategory.UTILITY),
        (SubmissionCategory.ENTRANCE, BuildCategory.ENTRANCE),
        (SubmissionCategory.OTHER, BuildCategory.OTHER),
    ],
)
async def test_adapter_creates_every_category_with_direct_account_ownership(
    submission_category: SubmissionCategory,
    build_category: BuildCategory,
) -> None:
    builds = FakeBuilds()

    result = await SubmissionBuildPreparation(builds, FakeTags(), FakeVersions()).prepare(
        _submission(submission_category)
    )

    build, arguments = builds.calls[0]
    assert isinstance(result, Build)
    assert result.id is None
    assert arguments["submitter_account_id"] == 17
    assert arguments["source_submission_draft_id"] == DRAFT_ID
    assert build.submitter_discord_id is None
    assert build.category is build_category
    assert build.display_name == "Workshop prototype"
    assert build.dimensions == (8, 9, 10)
    assert build.versions == ["Java 26.1.2"]
    assert build.version_spec == ">=26.1"


@pytest.mark.parametrize(
    "category", [SubmissionCategory.ENTRANCE, SubmissionCategory.UTILITY, SubmissionCategory.OTHER]
)
async def test_general_patterns_survive_prefill_durable_payload_and_build(category: SubmissionCategory) -> None:
    pattern = _definition("spiral", TagSemanticKind.PATTERN)
    source = BuildDraft(
        category=BuildCategory(category.value.title()),
        display_name="Tiny hidden staircase",
        patterns=[pattern.display_name],
        extra_info={"unknown_patterns": ["Spiral Staircase"]},
    )
    manifest = build_submission_manifest()
    projected = project_submission(
        source,
        manifest,
        {"approved_patterns": (ChoiceOption(pattern.stable_key, pattern.display_name),)},
        origin=SubmissionOrigin.DISCORD,
    )
    assert projected.unresolved == ()
    answers = manifest.apply_defaults(category.value, projected.answers, origin=SubmissionOrigin.DISCORD)
    snapshot = DraftSnapshot(DRAFT_ID, 1, manifest.schema_id, manifest.revision, category.value, answers=answers)
    normalized = normalize_submission(
        snapshot,
        answers,
        SubmissionArtifactReadiness(),
        None,
        origin=SubmissionOrigin.DISCORD,
    )
    payload = encode_submission(normalized)
    assert payload["payload_schema"] == 1
    restored = decode_submission(payload)
    assert restored == normalized

    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags((pattern,)), FakeVersions()).prepare(restored)
    assert isinstance(build, Build)
    assert build.patterns == [pattern.display_name]
    assert build.extra_info.get("unknown_patterns") == ["Spiral Staircase"]
    preview = preview_build(category.value, answers, {pattern.stable_key: pattern})
    assert preview is not None
    assert preview.patterns == build.patterns
    assert preview.extra_info.get("unknown_patterns") == ["Spiral Staircase"]


def test_empty_general_patterns_use_the_current_payload_shape() -> None:
    submission = _submission(SubmissionCategory.ENTRANCE)
    payload = encode_submission(submission)
    assert payload["payload_schema"] == 1
    assert payload["details"] == {"kind": "general", "pattern_keys": [], "pattern_proposals": []}
    assert "general_pattern_keys" not in payload
    assert decode_submission(payload) == submission


def test_general_pattern_payload_cannot_be_attached_to_a_door() -> None:
    submission = replace(
        _submission(SubmissionCategory.ENTRANCE),
        details=GeneralSubmissionDetails(pattern_proposals=("Spiral Staircase",)),
    )
    payload = encode_submission(submission)
    payload["category"] = "door"

    with pytest.raises(DataIntegrityError, match="persisted normalized submission"):
        decode_submission(payload)


async def test_adapter_preserves_taxonomy_timings_rights_and_opaque_artifact_origin() -> None:
    restriction = _definition("seamless", TagSemanticKind.RESTRICTION, restriction_type="wiring-placement")
    pattern = _definition("regular", TagSemanticKind.PATTERN)
    showcase = _definition("showcase_compact", TagSemanticKind.SHOWCASE)
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        taxonomy=SubmissionTaxonomy(
            restriction_keys=(restriction.stable_key,),
            restriction_proposals=("Novel restriction",),
            showcase_tags=(ShowcaseTag(showcase.stable_key),),
        ),
        artifacts=VerifiedSubmissionArtifacts((MEDIA_ID,), SCHEMATIC_ID),
        details=DoorSubmissionDetails(
            SubmissionDimensions(3, 4, 2),
            DoorOrientation.TRAPDOOR,
            pattern_keys=(pattern.stable_key,),
            pattern_proposals=("Hexagonal",),
            timing=DoorTiming(opening=4, visible_opening=3, closing=6, visible_closing=5),
        ),
    )
    builds = FakeBuilds()

    result = await SubmissionBuildPreparation(
        builds,
        FakeTags((restriction, pattern, showcase)),
        FakeVersions(),
    ).prepare(submission)

    build = builds.calls[0][0]
    assert isinstance(build, DoorBuild)
    assert build.door_dimensions == (3, 4, 2)
    assert build.orientation == "Trapdoor"
    assert build.patterns == ["Regular"]
    assert build.wiring_placement_restrictions == ["Seamless"]
    assert [assignment.definition.stable_key for assignment in build.tags] == ["showcase_compact"]
    assert (build.normal_opening_time, build.visible_opening_time) == (4, 3)
    assert (build.normal_closing_time, build.visible_closing_time) == (6, 5)
    extra_info = cast(Mapping[str, object], build.extra_info)
    assert extra_info.get("unknown_patterns") == ["Hexagonal"]
    origin = extra_info.get("source_submission")
    assert isinstance(origin, Mapping)
    assert origin["artifacts"] == {
        "normalized_media_upload_ids": [str(MEDIA_ID)],
        "schematic_id": str(SCHEMATIC_ID),
    }
    assert origin["schematic_policy"] == {
        "visibility": "reviewer_only",
        "license": None,
        "rights_attested": False,
    }


async def test_showcase_tags_become_assignments_with_their_parsed_values() -> None:
    speed = _definition("speed", TagSemanticKind.SHOWCASE, value_type=TagValueType.NUMERIC)
    compact = _definition("compact", TagSemanticKind.SHOWCASE)
    submission = replace(
        _submission(SubmissionCategory.OTHER),
        taxonomy=SubmissionTaxonomy(showcase_tags=(ShowcaseTag("compact"), ShowcaseTag("speed", "1.5"))),
    )
    builds = FakeBuilds()

    await SubmissionBuildPreparation(builds, FakeTags((speed, compact)), FakeVersions()).prepare(submission)

    build = builds.calls[0][0]
    assert [(tag.definition.stable_key, tag.value) for tag in build.tags] == [
        ("compact", None),
        ("speed", Decimal("1.5")),
    ]


@pytest.mark.parametrize(
    "tag", [ShowcaseTag("speed"), ShowcaseTag("speed", " "), ShowcaseTag("speed", "fast"), ShowcaseTag("compact", "1")]
)
async def test_a_showcase_value_its_tag_cannot_take_is_a_field_issue(tag: ShowcaseTag) -> None:
    speed = _definition("speed", TagSemanticKind.SHOWCASE, value_type=TagValueType.NUMERIC)
    compact = _definition("compact", TagSemanticKind.SHOWCASE)
    submission = replace(_submission(SubmissionCategory.OTHER), taxonomy=SubmissionTaxonomy(showcase_tags=(tag,)))
    builds = FakeBuilds()

    result = await SubmissionBuildPreparation(builds, FakeTags((speed, compact)), FakeVersions()).prepare(submission)

    assert result == BuildSubmissionRejected(
        (SubmissionAttentionIssue("showcase_tags", SubmissionAttentionReason.WRONG_TYPE),)
    )
    assert builds.calls == []


@pytest.mark.parametrize(
    ("revision", "answer"),
    [(9, [{"key": "compact"}, {"key": "speed", "value": "2"}]), (8, ["compact", "speed"])],
)
def test_normalization_reads_showcase_tags_of_either_form_revision(revision: int, answer: JSONValue) -> None:
    answers: dict[str, JSONValue] = {
        "schematic_visibility": "reviewer_only",
        "ai_generated": False,
        "showcase_tags": answer,
    }
    snapshot = DraftSnapshot(DRAFT_ID, 1, "build_submission.v1", revision, "other", answers=answers)

    normalized = normalize_submission(
        snapshot, answers, SubmissionArtifactReadiness(), None, origin=SubmissionOrigin.WEB
    )

    expected = ShowcaseTag("speed", "2") if revision == 9 else ShowcaseTag("speed")
    assert normalized.taxonomy.showcase_tags == (ShowcaseTag("compact"), expected)


def test_a_payload_queued_with_bare_showcase_keys_still_loads() -> None:
    submission = replace(
        _submission(SubmissionCategory.OTHER),
        taxonomy=SubmissionTaxonomy(showcase_tags=(ShowcaseTag("compact"), ShowcaseTag("speed", "2"))),
    )
    payload = encode_submission(submission)
    taxonomy = cast(dict[str, object], payload["taxonomy"])
    assert taxonomy["showcase_tags"] == [{"key": "compact", "value": None}, {"key": "speed", "value": "2"}]
    assert "showcase_tag_keys" not in taxonomy

    del taxonomy["showcase_tags"]
    taxonomy["showcase_tag_keys"] = ["compact"]

    assert decode_submission(payload).taxonomy.showcase_tags == (ShowcaseTag("compact"),)


async def test_adapter_persists_the_verified_public_sponsor_snapshot() -> None:
    sponsor = PublicSponsor(
        INSTALLATION_ID,
        display_name="Example server",
        address="play.example.test",
        website_url="https://example.test/server",
    )
    submission = replace(
        _submission(SubmissionCategory.OTHER),
        origin=SubmissionOrigin.PAPER,
        sponsor_attribution=True,
        source_installation_id=INSTALLATION_ID,
        sponsor=sponsor,
    )
    builds = FakeBuilds()

    result = await SubmissionBuildPreparation(builds, FakeTags(), FakeVersions()).prepare(submission)

    build = builds.calls[0][0]
    origin = cast(Mapping[str, object], build.extra_info)["source_submission"]
    assert build.sponsor == sponsor
    assert isinstance(origin, Mapping)
    assert origin["source_installation_id"] == str(INSTALLATION_ID)
    assert origin["sponsor"] == {
        "installation_id": str(INSTALLATION_ID),
        "display_name": "Example server",
        "address": "play.example.test",
        "description": None,
        "website_url": "https://example.test/server",
    }


async def test_retry_rejects_a_sponsor_snapshot_that_differs_from_the_existing_build() -> None:
    sponsor = PublicSponsor(INSTALLATION_ID, display_name="Original server")
    submission = replace(
        _submission(SubmissionCategory.OTHER),
        origin=SubmissionOrigin.PAPER,
        sponsor_attribution=True,
        source_installation_id=INSTALLATION_ID,
        sponsor=sponsor,
    )
    builds = FakeBuilds()
    target = SubmissionBuildPreparation(builds, FakeTags(), FakeVersions())
    first = await target.prepare(submission)
    assert isinstance(first, Build)
    builds.created[DRAFT_ID] = first

    changed = replace(
        submission,
        sponsor=PublicSponsor(INSTALLATION_ID, display_name="Changed server"),
    )
    result = await target.prepare(changed)

    assert result == BuildSubmissionRejected(
        (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)
    )


async def test_extender_timing_is_stored_on_the_extender_and_in_origin() -> None:
    submission = replace(
        _submission(SubmissionCategory.EXTENDER),
        details=ExtenderSubmissionDetails(
            ExtenderOrientation.VERTICAL_UP,
            5,
            timing=ExtenderTiming(extension=7, retraction=9),
        ),
    )
    builds = FakeBuilds()

    await SubmissionBuildPreparation(builds, FakeTags(), FakeVersions()).prepare(submission)

    build = builds.calls[0][0]
    assert isinstance(build, ExtenderBuild)
    assert build.orientation == "Upward"
    assert build.extension_length == 5
    assert (build.extension_time, build.retraction_time) == (7, 9)
    origin = cast(Mapping[str, object], build.extra_info).get("source_submission")
    assert isinstance(origin, Mapping)
    assert origin["timing"] == {"extension": 7, "retraction": 9}


async def test_retries_delegate_the_source_draft_key_and_return_the_existing_build() -> None:
    builds = FakeBuilds()
    target = SubmissionBuildPreparation(builds, FakeTags(), FakeVersions())
    submission = _submission(SubmissionCategory.OTHER)

    first = await target.prepare(submission)
    assert isinstance(first, Build)
    builds.created[DRAFT_ID] = first
    second = await target.prepare(submission)

    assert first == second
    assert len(builds.created) == 1
    assert [call[1]["source_submission_draft_id"] for call in builds.calls] == [DRAFT_ID]


async def test_retry_returns_existing_build_after_a_taxonomy_option_is_retired() -> None:
    pattern = _definition("regular", TagSemanticKind.PATTERN)
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        details=DoorSubmissionDetails(
            SubmissionDimensions(2, 3, 1),
            DoorOrientation.DOOR,
            pattern_keys=(pattern.stable_key,),
        ),
    )
    builds = FakeBuilds()
    tags = FakeTags((pattern,))
    versions = FakeVersions()
    target = SubmissionBuildPreparation(builds, tags, versions)
    first = await target.prepare(submission)
    assert isinstance(first, Build)
    builds.created[DRAFT_ID] = first
    tags.definitions = ()
    versions.versions = ()

    second = await target.prepare(submission)

    assert second == first
    assert len(builds.calls) == 1


async def test_unknown_source_version_is_actionable_and_never_delegated_to_persistence() -> None:
    builds = FakeBuilds()
    submission = replace(_submission(SubmissionCategory.OTHER), source_version="Java 26.1.20")

    result = await SubmissionBuildPreparation(builds, FakeTags(), FakeVersions()).prepare(submission)

    assert result == BuildSubmissionRejected(
        (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
    )
    assert builds.calls == []


@pytest.mark.parametrize(
    ("submission", "expected_issue"),
    [
        (
            replace(
                _submission(SubmissionCategory.DOOR),
                details=DoorSubmissionDetails(
                    SubmissionDimensions(2, 3, 1),
                    DoorOrientation.DOOR,
                    pattern_keys=("missing_pattern",),
                ),
            ),
            SubmissionAttentionIssue("patterns", SubmissionAttentionReason.UNKNOWN_OPTION),
        ),
        (
            replace(_submission(SubmissionCategory.OTHER), display_name="x" * 121),
            SubmissionAttentionIssue("display_name", SubmissionAttentionReason.TOO_LONG),
        ),
    ],
)
async def test_user_repairable_validation_failures_become_stable_attention_issues(
    submission: NormalizedSubmission,
    expected_issue: SubmissionAttentionIssue,
) -> None:
    result = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(submission)

    assert isinstance(result, BuildSubmissionRejected)
    assert expected_issue in result.issues


async def test_structured_build_rejection_becomes_target_attention() -> None:
    target = SubmissionBuildPreparation(
        FakeBuilds(error=InvalidBuildError("invalid")),
        FakeTags(),
        FakeVersions(),
    )

    result = await target.prepare(_submission(SubmissionCategory.UTILITY))

    assert result == BuildSubmissionRejected(
        (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)
    )


async def test_a_door_without_depth_build_size_or_exported_version_still_becomes_a_build() -> None:
    # Form revision 4 leaves these optional; the payload codec and adapter must carry the gaps through.
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        schema_revision=4,
        origin=SubmissionOrigin.DISCORD,
        capture_dimensions=SubmissionDimensions(None, None, None),
        source_version=None,
        details=DoorSubmissionDetails(SubmissionDimensions(2, 3, None), DoorOrientation.DOOR),
    )
    assert decode_submission(encode_submission(submission)) == submission
    builds = FakeBuilds()

    result = await SubmissionBuildPreparation(builds, FakeTags(), FakeVersions()).prepare(submission)

    assert isinstance(result, DoorBuild)
    assert result.versions == []
    assert (result.width, result.height, result.depth) == (None, None, None)
    assert (result.door_width, result.door_height, result.door_depth) == (2, 3, None)


def test_submission_dimensions_reject_parts_below_one() -> None:
    with pytest.raises(ValidationError):
        SubmissionDimensions(2, 0, None)


def test_preview_build_accepts_partial_answers_and_maps_known_taxonomy() -> None:
    restriction = _definition("seamless", TagSemanticKind.RESTRICTION, restriction_type="wiring-placement")
    pattern = _definition("regular", TagSemanticKind.PATTERN)
    speed = _definition("speed", TagSemanticKind.SHOWCASE, value_type=TagValueType.NUMERIC)
    compact = _definition("compact", TagSemanticKind.SHOWCASE)
    build = preview_build(
        "door",
        {
            "creators": ["Builder"],
            "opening_width": 2,
            "opening_height": 2,
            "door_orientation": "trapdoor",
            "restrictions": ["seamless", "retired"],
            "patterns": ["regular"],
            "pattern_proposals": ["Spiral"],
            "showcase_tags": [{"key": "speed", "value": "3"}, {"key": "compact", "value": "1"}],
            "ai_generated": True,
        },
        {definition.stable_key: definition for definition in (restriction, pattern, speed, compact)},
    )
    assert isinstance(build, DoorBuild)
    assert build.id is None
    assert build.submission_status is Status.PENDING
    assert (build.door_width, build.door_height, build.door_depth) == (2, 2, None)
    assert build.orientation == "Trapdoor"
    assert build.wiring_placement_restrictions == [restriction.display_name]
    assert build.patterns == [pattern.display_name]
    # A value the tag cannot take leaves the tag off the preview instead of failing it.
    assert [(tag.definition.stable_key, tag.value) for tag in build.tags] == [("speed", Decimal(3))]
    assert build.ai_generated
    assert build.versions == []


def test_preview_accepts_unknown_title_facts() -> None:
    door = preview_build("door", {"opening_width": 2, "opening_height": 2}, {})
    extender = preview_build("extender", {"movement_orientation": "horizontal"}, {})
    other = preview_build("other", {}, {})

    assert isinstance(door, DoorBuild)
    assert door.door_depth is None
    assert isinstance(extender, ExtenderBuild)
    assert extender.extension_length is None
    assert isinstance(other, Build)
    assert other.category is BuildCategory.OTHER


@pytest.mark.parametrize("category", [SubmissionCategory.DOOR, SubmissionCategory.EXTENDER])
async def test_unknown_metadata_survives_payload_build_and_title(category: SubmissionCategory) -> None:
    details = (
        DoorSubmissionDetails(SubmissionDimensions(None, None, None), None)
        if category is SubmissionCategory.DOOR
        else ExtenderSubmissionDetails(None, None)
    )
    submission = replace(
        _submission(category),
        credits=(),
        source_version=None,
        capture_dimensions=SubmissionDimensions(None, None, None),
        details=details,
    )
    restored = decode_submission(encode_submission(submission))
    assert restored == submission
    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(restored)
    assert isinstance(build, DoorBuild | ExtenderBuild)
    assert build.orientation is None
    assert build.creators_ign == []
    title = format_build_display_title(build, markdown=False)
    assert "None" not in title
    assert "?" in title


async def test_typed_credits_survive_durable_payload_and_build_preparation() -> None:
    credits = (
        Credit(
            name="Alex",
            kind=CreditKind.DISCORD,
            subject="123",
            username="alex_d",
            attribution=CreditAttribution.ASSUMED_SENDER,
            source_message_id=456,
        ),
        Credit(name="Alex", kind=CreditKind.MINECRAFT_JAVA, username="Alex", role=CreditRole.LAYOUT),
        Credit(name="Alex", role=CreditRole.CONTRIBUTOR),
    )
    submission = replace(_submission(SubmissionCategory.OTHER), credits=credits)
    encoded = encode_submission(submission)
    assert encoded["payload_schema"] == 1
    assert "creators" not in encoded
    decoded = decode_submission(encoded)
    assert decoded.credits == credits
    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(decoded)
    assert isinstance(build, Build)
    assert build.credits == list(credits)
    assert build.creators_ign == ["Alex"]


async def test_external_links_survive_payload_and_build_preparation_without_becoming_uploads() -> None:
    links = (
        SubmissionLink("https://imgur.com/a/example", "image"),
        SubmissionLink("https://youtu.be/example", "video"),
        SubmissionLink("https://example.test/world.zip", "world-download"),
    )
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        links=links,
        details=DoorSubmissionDetails(
            SubmissionDimensions(4, 4, 1), DoorOrientation.DOOR, timing=DoorTiming(27, 25, 27, 25)
        ),
    )
    encoded = encode_submission(submission)
    assert encoded["payload_schema"] == 1
    restored = decode_submission(encoded)
    assert restored.links == links
    assert restored.artifacts == VerifiedSubmissionArtifacts()
    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(restored)
    assert isinstance(build, DoorBuild)
    assert [(link.url, link.media_type) for link in build.links] == [(link.url, link.media_type) for link in links]
    assert (build.normal_opening_time, build.visible_opening_time) == (27, 25)
    assert (build.normal_closing_time, build.visible_closing_time) == (27, 25)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///app/world.zip", "/relative.zip", "https://host/a b"])
def test_external_link_payloads_reject_non_web_references(url: str) -> None:
    with pytest.raises(ValidationError, match="absolute HTTP"):
        SubmissionLink(url, "image")


async def test_expansion_survives_finalization_payload_and_build_preparation() -> None:
    document = expansion_document()
    submission = _submission(SubmissionCategory.DOOR)
    assert isinstance(submission.details, DoorSubmissionDetails)
    submission = replace(submission, details=replace(submission.details, expansion=document))
    decoded = decode_submission(encode_submission(submission))
    assert decoded == submission
    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(decoded)
    assert isinstance(build, DoorBuild)
    assert build.expansion == document


async def test_discord_compatibility_survives_durable_payload_without_a_capture_version() -> None:
    versions = FakeVersions((MinecraftVersion("Java", 1, 16, 0), MinecraftVersion("Java", 1, 16, 1)))
    versions.resolve_spec = AsyncMock(return_value=["Java 1.16.0", "Java 1.16.1", "Java 1.16.0"])
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        origin=SubmissionOrigin.DISCORD,
        source_version=None,
        version_compatibility="Java 1.16+",
    )
    restored = decode_submission(encode_submission(submission))
    result = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), versions).prepare(restored)
    assert isinstance(result, Build)
    assert result.versions == ["Java 1.16.0", "Java 1.16.1"]
    assert result.version_spec == "Java 1.16+"


@pytest.mark.parametrize("error", [ValueError("invalid number"), InvalidVersionError("invalid range")])
async def test_unparseable_compatibility_keeps_the_claim_without_failing_the_build(error: Exception) -> None:
    versions = FakeVersions()
    versions.resolve_spec = AsyncMock(side_effect=error)
    submission = replace(
        _submission(SubmissionCategory.DOOR), source_version=None, version_compatibility="Java unknown"
    )
    result = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), versions).prepare(submission)
    assert isinstance(result, Build)
    assert result.versions == []
    assert result.version_spec == "Java unknown"


async def test_restriction_categories_survive_durable_payload_and_preparation() -> None:
    grouped: tuple[tuple[RestrictionField, str], ...] = (
        ("wiring_placement_restrictions", "Seamless"),
        ("animated_restrictions", "Full Sync"),
        ("component_restrictions", "Contained"),
    )
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        taxonomy=SubmissionTaxonomy(restriction_proposals=("Unclassified",), restriction_proposal_fields=grouped),
    )
    payload = encode_submission(submission)
    assert payload["payload_schema"] == 1
    restored = decode_submission(payload)
    assert restored == submission
    build = await SubmissionBuildPreparation(FakeBuilds(), FakeTags(), FakeVersions()).prepare(restored)
    assert isinstance(build, Build)
    assert build.extra_info.get("unknown_restrictions") == {
        "wiring_placement_restrictions": ["Seamless"],
        "animated_restrictions": ["Full Sync"],
        "component_restrictions": ["Contained"],
        "miscellaneous_restrictions": ["Unclassified"],
    }

"""Translation from normalized submissions into account-keyed builds."""

from collections.abc import Mapping
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest

from squid.builds.domain import Build, BuildCategory, DoorBuild, ExtenderBuild, OtherBuild
from squid.builds.errors import InvalidBuildError
from squid.sponsors import PublicSponsor
from squid.submissions.application import BuildSubmissionRejectedError
from squid.submissions.domain import (
    DoorOrientation,
    DoorSubmissionDetails,
    DoorTiming,
    ExtenderOrientation,
    ExtenderSubmissionDetails,
    ExtenderTiming,
    GeneralSubmissionDetails,
    NormalizedSubmission,
    SchematicRightsPolicy,
    SubmissionAttentionIssue,
    SubmissionAttentionReason,
    SubmissionCategory,
    SubmissionDimensions,
    SubmissionOrigin,
    SubmissionSchematicVisibility,
    SubmissionTaxonomy,
    VerifiedSubmissionArtifacts,
)
from squid.submissions.infrastructure.build_target import CanonicalBuildSubmissionWriter
from squid.tags.domain import (
    TagAuthority,
    TagDefinition,
    TagModerationStatus,
    TagSemanticKind,
    TagValueType,
)
from squid.versions.domain import MinecraftVersion

DRAFT_ID = UUID("00000000-0000-4000-8000-000000000501")
MEDIA_ID = UUID("00000000-0000-4000-8000-000000000502")
SCHEMATIC_ID = UUID("00000000-0000-4000-8000-000000000503")
INSTALLATION_ID = UUID("00000000-0000-4000-8000-000000000504")


class FakeBuilds:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[Build, dict[str, object]]] = []
        self.created: dict[UUID, Build] = {}

    async def get_by_source_submission_draft_id(self, draft_id: UUID) -> Build | None:
        return self.created.get(draft_id)

    async def submit_for_account(self, build: Build, **kwargs: object) -> Build:
        self.calls.append((build, kwargs))
        if self.error is not None:
            raise self.error
        draft_id = kwargs["source_submission_draft_id"]
        assert isinstance(draft_id, UUID)
        if draft_id in self.created:
            return self.created[draft_id]
        build.id = 41
        account_id = kwargs["submitter_account_id"]
        assert isinstance(account_id, int)
        build.submitter_account_id = account_id
        build.source_submission_draft_id = draft_id
        build.display_name = kwargs["display_name"] if isinstance(kwargs["display_name"], str) else None
        self.created[draft_id] = build
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
        schema_revision=1,
        category=category,
        display_name="Workshop prototype",
        description="A synchronized submission",
        creators=("Builder",),
        capture_dimensions=SubmissionDimensions(8, 9, 10),
        source_version="Java 26.1.2",
        version_compatibility=">=26.1",
        taxonomy=SubmissionTaxonomy(),
        schematic_policy=SchematicRightsPolicy(
            visibility=SubmissionSchematicVisibility.REVIEWER_ONLY,
            license=None,
            rights_attested=False,
            include_inventories=False,
            include_free_text=False,
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

    result = await CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions()).create_or_get(
        _submission(submission_category)
    )

    build, arguments = builds.calls[0]
    assert result.build_id == 41
    assert arguments["submitter_account_id"] == 17
    assert arguments["source_submission_draft_id"] == DRAFT_ID
    assert build.submitter_discord_id is None
    assert build.category is build_category
    assert build.display_name == "Workshop prototype"
    assert build.dimensions == (8, 9, 10)
    assert build.versions == ["Java 26.1.2"]
    assert build.version_spec == ">=26.1"


async def test_adapter_preserves_taxonomy_timings_rights_and_opaque_artifact_provenance() -> None:
    restriction = _definition("seamless", TagSemanticKind.RESTRICTION, restriction_type="wiring-placement")
    pattern = _definition("regular", TagSemanticKind.PATTERN)
    showcase = _definition("showcase_compact", TagSemanticKind.SHOWCASE)
    submission = replace(
        _submission(SubmissionCategory.DOOR),
        taxonomy=SubmissionTaxonomy(
            restriction_keys=(restriction.stable_key,),
            restriction_proposals=("Novel restriction",),
            showcase_tag_keys=(showcase.stable_key,),
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

    result = await CanonicalBuildSubmissionWriter(
        builds,
        FakeTags((restriction, pattern, showcase)),
        FakeVersions(),
    ).create_or_get(submission)

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
    provenance = extra_info.get("submission_provenance")
    assert isinstance(provenance, Mapping)
    assert provenance["artifacts"] == {
        "normalized_media_upload_ids": [str(MEDIA_ID)],
        "sanitized_schematic_id": str(SCHEMATIC_ID),
    }
    assert provenance["schematic_policy"] == {
        "visibility": "reviewer_only",
        "license": None,
        "rights_attested": False,
        "include_inventories": False,
        "include_free_text": False,
    }


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

    result = await CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions()).create_or_get(submission)

    build = builds.calls[0][0]
    provenance = cast(Mapping[str, object], build.extra_info)["submission_provenance"]
    assert build.sponsor == sponsor
    assert isinstance(provenance, Mapping)
    assert provenance["source_installation_id"] == str(INSTALLATION_ID)
    assert provenance["sponsor"] == {
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
    target = CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions())
    await target.create_or_get(submission)

    changed = replace(
        submission,
        sponsor=PublicSponsor(INSTALLATION_ID, display_name="Changed server"),
    )
    with pytest.raises(BuildSubmissionRejectedError) as error:
        await target.create_or_get(changed)

    assert error.value.issues == (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)


async def test_retry_race_rejects_a_persisted_build_with_different_sponsor_provenance() -> None:
    sponsor = PublicSponsor(INSTALLATION_ID, display_name="Expected server")
    submission = replace(
        _submission(SubmissionCategory.OTHER),
        origin=SubmissionOrigin.PAPER,
        sponsor_attribution=True,
        source_installation_id=INSTALLATION_ID,
        sponsor=sponsor,
    )

    class RacingBuilds(FakeBuilds):
        async def submit_for_account(self, build: Build, **kwargs: object) -> Build:
            return OtherBuild(
                id=41,
                submitter_account_id=17,
                source_submission_draft_id=DRAFT_ID,
                sponsor=None,
            )

    with pytest.raises(BuildSubmissionRejectedError) as error:
        await CanonicalBuildSubmissionWriter(RacingBuilds(), FakeTags(), FakeVersions()).create_or_get(submission)

    assert error.value.issues == (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)


async def test_extender_timing_is_retained_when_the_legacy_build_columns_have_no_slot() -> None:
    submission = replace(
        _submission(SubmissionCategory.EXTENDER),
        details=ExtenderSubmissionDetails(
            ExtenderOrientation.VERTICAL_UP,
            5,
            timing=ExtenderTiming(extension=7, retraction=9),
        ),
    )
    builds = FakeBuilds()

    await CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions()).create_or_get(submission)

    build = builds.calls[0][0]
    assert isinstance(build, ExtenderBuild)
    assert build.orientation == "Upward"
    assert build.extension_length == 5
    provenance = cast(Mapping[str, object], build.extra_info).get("submission_provenance")
    assert isinstance(provenance, Mapping)
    assert provenance["timing"] == {"extension": 7, "retraction": 9}


async def test_retries_delegate_the_source_draft_key_and_return_the_existing_build() -> None:
    builds = FakeBuilds()
    target = CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions())
    submission = _submission(SubmissionCategory.OTHER)

    first = await target.create_or_get(submission)
    second = await target.create_or_get(submission)

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
    target = CanonicalBuildSubmissionWriter(builds, tags, versions)
    first = await target.create_or_get(submission)
    tags.definitions = ()
    versions.versions = ()

    second = await target.create_or_get(submission)

    assert second == first
    assert len(builds.calls) == 1


async def test_unknown_source_version_is_actionable_and_never_delegated_to_persistence() -> None:
    builds = FakeBuilds()
    submission = replace(_submission(SubmissionCategory.OTHER), source_version="Java 26.1.20")

    with pytest.raises(BuildSubmissionRejectedError) as error:
        await CanonicalBuildSubmissionWriter(builds, FakeTags(), FakeVersions()).create_or_get(submission)

    assert error.value.issues == (SubmissionAttentionIssue("source_version", SubmissionAttentionReason.UNKNOWN_OPTION),)
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
    with pytest.raises(BuildSubmissionRejectedError) as error:
        await CanonicalBuildSubmissionWriter(FakeBuilds(), FakeTags(), FakeVersions()).create_or_get(submission)

    assert expected_issue in error.value.issues


async def test_structured_build_rejection_becomes_target_attention() -> None:
    target = CanonicalBuildSubmissionWriter(
        FakeBuilds(error=InvalidBuildError("invalid")),
        FakeTags(),
        FakeVersions(),
    )

    with pytest.raises(BuildSubmissionRejectedError) as error:
        await target.create_or_get(_submission(SubmissionCategory.UTILITY))

    assert error.value.issues == (SubmissionAttentionIssue("submission", SubmissionAttentionReason.TARGET_REJECTED),)

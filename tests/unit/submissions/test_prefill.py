"""Inference prefill cannot fabricate required facts or promote unknown taxonomy."""

from uuid import uuid4

from squid.builds.domain import BuildCategory, BuildDraft
from squid.builds.models import BuildLink
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.application.prefill import project_submission
from squid.submissions.domain import ChoiceOption, DraftSnapshot, SubmissionOrigin
from squid.submissions.domain.finalization import SubmissionArtifactReadiness
from squid.submissions.domain.normalization import normalize_submission


def test_missing_category_is_retained_for_correction() -> None:
    result = project_submission(BuildDraft(), build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)
    assert result.category is None
    assert result.answers == {}
    assert result.unresolved == ("category",)


def test_known_labels_become_keys_and_unknown_names_remain_proposals() -> None:
    draft = BuildDraft(
        category=BuildCategory.DOOR,
        patterns=["Regular", "New pattern"],
        ai_generated=True,
        component_restrictions=["Observerless"],
        version_spec="Java 1.21.0",
    )
    result = project_submission(
        draft,
        build_submission_manifest(),
        {
            "approved_patterns": (ChoiceOption("regular", "Regular"),),
            "approved_restrictions": (ChoiceOption("observerless", "Observerless"),),
            "approved_source_versions": (ChoiceOption("Java 1.21.0", "Java 1.21.0"),),
        },
        origin=SubmissionOrigin.WEB,
    )
    assert result.answers["patterns"] == ["regular"]
    assert result.answers["pattern_proposals"] == ["New pattern"]
    assert result.answers["restrictions"] == ["observerless"]
    assert result.answers["source_version"] == "Java 1.21.0"
    assert result.answers["schematic_visibility"] == "reviewer_only"
    assert {"include_inventories", "include_free_text"}.isdisjoint(result.answers)
    assert "rights_attestation" not in result.answers


def test_discord_drafts_do_not_keep_an_exported_version() -> None:
    # From form revision 4 only capture clients are asked which version a build was exported from.
    draft = BuildDraft(category=BuildCategory.DOOR, version_spec="Java 1.21.0")
    result = project_submission(
        draft,
        build_submission_manifest(),
        {"approved_source_versions": (ChoiceOption("Java 1.21.0", "Java 1.21.0"),)},
        origin=SubmissionOrigin.DISCORD,
    )
    assert "source_version" not in result.answers
    assert result.answers["version_compatibility"] == "Java 1.21.0"


def test_compatibility_range_is_not_an_exact_source_version() -> None:
    draft = BuildDraft(category=BuildCategory.DOOR, version_spec="1.20+", versions=["Java 1.21.0"], door_width=2)
    result = project_submission(
        draft,
        build_submission_manifest(),
        {
            "approved_source_versions": (ChoiceOption("Java 1.21.0", "Java 1.21.0"),),
        },
        origin=SubmissionOrigin.WEB,
    )
    assert "source_version" not in result.answers
    assert "opening_height" not in result.answers
    assert "door_orientation" not in result.answers
    assert result.answers["version_compatibility"] == "1.20+"


def test_invalid_inferred_values_are_flagged_for_correction() -> None:
    result = project_submission(
        BuildDraft(category=BuildCategory.OTHER, width=-1),
        build_submission_manifest(),
        {},
        origin=SubmissionOrigin.DISCORD,
    )
    assert "capture_width" not in result.answers
    assert result.unresolved == ("capture_width",)


def test_names_inference_could_not_resolve_become_proposals() -> None:
    draft = BuildDraft(
        category=BuildCategory.DOOR,
        patterns=["Regular"],
        component_restrictions=["Observerless"],
        extra_info={
            "unknown_restrictions": {"wiring_placement_restrictions": ["Floating wires"]},
            "unknown_patterns": ["Spiral"],
        },
    )
    result = project_submission(
        draft,
        build_submission_manifest(),
        {
            "approved_patterns": (ChoiceOption("regular", "Regular"),),
            "approved_restrictions": (ChoiceOption("observerless", "Observerless"),),
        },
        origin=SubmissionOrigin.DISCORD,
    )
    assert result.answers["restrictions"] == ["observerless"]
    assert result.answers["wiring_restriction_proposals"] == ["Floating wires"]
    assert result.answers["patterns"] == ["regular"]
    assert result.answers["pattern_proposals"] == ["Spiral"]


def test_external_links_are_form_answers_in_revision_six_and_remain_references_on_older_forms() -> None:
    source = BuildDraft(
        category=BuildCategory.DOOR,
        links=[
            BuildLink(url="https://imgur.com/a/example", media_type="image"),
            BuildLink(url="https://youtu.be/example", media_type="video"),
            BuildLink(url="https://example.test/world.zip", media_type="world-download"),
        ],
    )
    result = project_submission(source, build_submission_manifest(), {}, origin=SubmissionOrigin.DISCORD)
    assert result.answers["image_urls"] == ["https://imgur.com/a/example"]
    assert result.answers["video_urls"] == ["https://youtu.be/example"]
    assert result.answers["world_download_urls"] == ["https://example.test/world.zip"]
    old = project_submission(source, build_submission_manifest(revision=5), {}, origin=SubmissionOrigin.DISCORD)
    assert {"image_urls", "video_urls", "world_download_urls"}.isdisjoint(old.answers)


def test_unknown_restrictions_retain_their_categories_through_normalization() -> None:
    source = BuildDraft(
        category=BuildCategory.DOOR,
        extra_info={
            "unknown_restrictions": {
                "wiring_placement_restrictions": ["Seamless"],
                "animated_restrictions": ["Full Sync"],
                "component_restrictions": ["Contained"],
                "miscellaneous_restrictions": ["Other"],
            }
        },
    )
    manifest = build_submission_manifest()
    projected = project_submission(source, manifest, {}, origin=SubmissionOrigin.DISCORD)
    assert projected.unresolved == ()
    answers = manifest.apply_defaults("door", projected.answers, origin=SubmissionOrigin.DISCORD)
    snapshot = DraftSnapshot(uuid4(), 1, manifest.schema_id, manifest.revision, "door", answers=answers)
    normalized = normalize_submission(
        snapshot, answers, SubmissionArtifactReadiness(), None, origin=SubmissionOrigin.DISCORD
    )
    assert normalized.taxonomy.restriction_proposal_fields == (
        ("wiring_placement_restrictions", "Seamless"),
        ("animated_restrictions", "Full Sync"),
        ("component_restrictions", "Contained"),
    )
    assert normalized.taxonomy.restriction_proposals == ("Other",)
    old = project_submission(source, build_submission_manifest(revision=6), {}, origin=SubmissionOrigin.DISCORD)
    assert old.answers["restriction_proposals"] == ["Seamless", "Full Sync", "Contained", "Other"]

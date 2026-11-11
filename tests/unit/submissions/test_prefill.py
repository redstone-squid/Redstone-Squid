"""Inference prefill cannot fabricate required facts or promote unknown taxonomy."""

from squid.builds.domain import BuildCategory, BuildDraft
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.application.prefill import project_submission
from squid.submissions.domain import ChoiceOption, SubmissionOrigin


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
    assert result.answers["restriction_proposals"] == ["Floating wires"]
    assert result.answers["patterns"] == ["regular"]
    assert result.answers["pattern_proposals"] == ["Spiral"]

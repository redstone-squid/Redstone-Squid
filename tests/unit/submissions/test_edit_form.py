"""The build form's two projections: submitting a new build and editing a saved one."""

from squid.submissions.application.forms import build_edit_manifest, build_submission_manifest
from squid.submissions.domain import FormManifest

SUBMISSION_ONLY = {
    "schematic_visibility",
    "schematic_license",
    "rights_attestation",
    "ai_generated",
    "sponsor_attribution",
    "restriction_proposals",
    "pattern_proposals",
    "source_version",
}
EDIT_ONLY = {"versions", "image_urls", "video_urls", "world_download_urls", "server_ip", "coordinates"}


def _field_ids(manifest: FormManifest, category: str) -> set[str]:
    return {field.id for field in manifest.fields_for(category)}


def test_each_projection_shows_only_its_own_fields() -> None:
    submission, edit = build_submission_manifest(), build_edit_manifest()

    for category in ("door", "extender", "utility", "entrance", "other"):
        # Only doors and extenders have patterns, so only they ask for pattern proposals.
        expected = SUBMISSION_ONLY if category in {"door", "extender"} else SUBMISSION_ONLY - {"pattern_proposals"}
        assert _field_ids(submission, category) >= expected
        assert not EDIT_ONLY & _field_ids(submission, category)
        assert _field_ids(edit, category) >= EDIT_ONLY
        assert not SUBMISSION_ONLY & _field_ids(edit, category)


def test_both_projections_share_section_order_and_titles() -> None:
    submission, edit = build_submission_manifest(), build_edit_manifest()

    shared = [(section.id, section.title) for section in submission.common_sections if section.id != "rights_privacy"]
    edited = [(section.id, section.title) for section in edit.common_sections]

    # Only the context section is retitled, because editing leaves just the completion date.
    assert [section_id for section_id, _ in edited] == [*(section_id for section_id, _ in shared), "links_location"]
    assert dict(edited)["identity"] == dict(shared)["identity"]
    assert [title for section_id, title in edited if section_id == "submission_context"] == ["About this build"]


def test_editing_uses_the_limits_a_saved_build_may_hold() -> None:
    submitted = {field.id: field for field in build_submission_manifest().fields_for("door")}
    edited = {field.id: field for field in build_edit_manifest().fields_for("door")}

    assert submitted["description"].constraints.max_length == 4_000
    assert edited["description"].constraints.max_length == 10_000
    assert submitted["creators"].required
    assert not edited["creators"].required
    assert edited["completion"].constraints.max_length == 100

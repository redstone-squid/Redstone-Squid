"""The saved-build edit form follows the submission form it is derived from."""

from squid.submissions.application.edit_form import SUBMISSION_ONLY_FIELDS, build_edit_form
from squid.submissions.application.forms import build_submission_manifest
from squid.submissions.domain import FormManifest


def _field_ids(manifest: FormManifest, category: str) -> list[str]:
    return [field.id for field in manifest.fields_for(category)]


def test_sections_keep_the_submission_order_and_titles() -> None:
    submission = build_submission_manifest()
    edit = build_edit_form()

    shared = [(section.id, section.title) for section in submission.common_sections if section.id != "rights_privacy"]
    edited = [(section.id, section.title) for section in edit.common_sections]

    # The context section keeps its place but describes the build rather than the submission.
    assert [section_id for section_id, _ in edited] == [*(section_id for section_id, _ in shared), "links_location"]
    assert [title for section_id, title in edited if section_id == "identity"] == ["Name and creators"]


def test_submission_only_fields_are_left_out_for_every_category() -> None:
    edit = build_edit_form()

    for category in ("door", "extender", "utility", "entrance", "other"):
        assert not SUBMISSION_ONLY_FIELDS & set(_field_ids(edit, category))


def test_a_saved_build_edits_its_versions_links_and_extender_timings() -> None:
    edit = build_edit_form()

    assert {"versions", "image_urls", "server_ip", "showcase_tags"} <= set(_field_ids(edit, "door"))
    assert {"extension_time", "retraction_time"} <= set(_field_ids(edit, "extender"))


def test_limits_fit_what_a_saved_build_may_hold() -> None:
    fields = {field.id: field for field in build_edit_form().fields_for("door")}

    assert fields["description"].constraints.max_length == 10_000
    assert fields["creators"].constraints.max_items == 100
    assert not fields["creators"].required
    assert fields["completion"].constraints.max_length == 100

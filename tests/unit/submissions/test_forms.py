from uuid import UUID

import pytest

from squid.core.errors import JSONValue
from squid.submissions.application import (
    CURRENT_SUBMISSION_PROTOCOL,
    FormOptionSet,
    SubmissionFormService,
    build_submission_manifest,
)
from squid.submissions.domain import (
    ChoiceOption,
    DraftChange,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    SubmissionOrigin,
)


class FakeOptionCatalog:
    async def options(self, source: str, category: str, *, locale: str | None) -> FormOptionSet:
        assert locale == "en"
        return FormOptionSet(source, category, 7, (ChoiceOption("slimestone", "Slimestone"),))


def _valid_common_answers() -> dict[str, JSONValue]:
    return {
        "capture_width": 5,
        "capture_height": 6,
        "capture_depth": 7,
        "source_version": "26.1.2",
        "creators": ["Builder"],
        "schematic_visibility": "reviewer_only",
    }


def test_manifest_has_stable_categories_without_a_type_label() -> None:
    manifest = build_submission_manifest("en")

    assert manifest.minimum_protocol == CURRENT_SUBMISSION_PROTOCOL
    assert [category.code for category in manifest.categories] == [
        "door",
        "extender",
        "utility",
        "entrance",
        "other",
    ]
    assert all(
        "type_label" not in {field.id for field in manifest.fields_for(category.code)}
        for category in manifest.categories
    )
    assert "display_name" in {field.id for field in manifest.fields_for("other")}


def test_manifest_validates_category_fields_and_server_defaults() -> None:
    manifest = build_submission_manifest("en")
    answers = _valid_common_answers() | {
        "opening_width": 3,
        "opening_height": 3,
        "opening_depth": 1,
        "door_orientation": "door",
    }

    assert manifest.validate_answers("door", answers, origin=SubmissionOrigin.FABRIC) == {}
    assert manifest.apply_defaults("door", answers, origin=SubmissionOrigin.FABRIC) | {
        "ai_generated": False,
        "include_free_text": True,
        "include_inventories": True,
    } == manifest.apply_defaults("door", answers, origin=SubmissionOrigin.FABRIC)


def test_public_download_requires_license_and_true_rights_attestation() -> None:
    manifest = build_submission_manifest("en")
    answers = _valid_common_answers() | {
        "schematic_visibility": "public_download",
        "schematic_license": "cc_by_4_0",
        "rights_attestation": False,
    }

    assert manifest.validate_answers("other", answers, origin=SubmissionOrigin.WEB) == {
        "rights_attestation": "required_value"
    }


def test_partial_validation_allows_incomplete_draft_but_rejects_wrong_values() -> None:
    manifest = build_submission_manifest("en")

    assert manifest.validate_answers(
        "extender",
        {"extension_length": 0},
        origin=SubmissionOrigin.DISCORD,
        require_complete=False,
    ) == {"extension_length": "below_minimum"}


@pytest.mark.asyncio
async def test_dynamic_options_remain_category_aware() -> None:
    service = SubmissionFormService(FakeOptionCatalog())

    option_set = await service.options("approved_patterns", "door", locale="en")

    assert service.manifest(locale="en").category("door").code == "door"
    assert option_set == FormOptionSet(
        "approved_patterns",
        "door",
        7,
        (ChoiceOption("slimestone", "Slimestone"),),
    )


def test_draft_field_operations_are_atomic_and_revisioned() -> None:
    draft = DraftSnapshot(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        owner_account_id=42,
        schema_id="build_submission.v1",
        schema_revision=1,
        category="other",
        answers={"display_name": "First"},
    )
    change = DraftChange(
        base_revision=0,
        client_instance_id="fabric:device-1",
        idempotency_key="edit-0001",
        operations=(
            FieldOperation(
                UUID("00000000-0000-4000-8000-000000000002"),
                "display_name",
                FieldOperationKind.SET,
                "Second",
            ),
            FieldOperation(
                UUID("00000000-0000-4000-8000-000000000003"),
                "description",
                FieldOperationKind.SET,
                "Description",
            ),
        ),
    )

    updated = draft.apply(change)

    assert draft.answers == {"display_name": "First"}
    assert updated.revision == 1
    assert updated.answers == {"display_name": "Second", "description": "Description"}

    with pytest.raises(DraftRevisionConflictError) as error:
        updated.apply(change)
    assert error.value.public_context == {"expected_revision": 0, "actual_revision": 1}


def test_draft_lifecycle_prevents_edits_while_processing() -> None:
    draft = DraftSnapshot(
        id=UUID("00000000-0000-4000-8000-000000000004"),
        owner_account_id=42,
        schema_id="build_submission.v1",
        schema_revision=1,
        category="other",
    ).transition(DraftStatus.PROCESSING)

    with pytest.raises(ValueError, match="cannot be edited"):
        draft.apply(
            DraftChange(
                base_revision=0,
                client_instance_id="web:browser-1",
                idempotency_key="edit-0002",
                operations=(
                    FieldOperation(
                        UUID("00000000-0000-4000-8000-000000000005"),
                        "description",
                        FieldOperationKind.SET,
                        "Late edit",
                    ),
                ),
            )
        )

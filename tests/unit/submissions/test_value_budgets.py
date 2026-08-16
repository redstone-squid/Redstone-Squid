"""Resource budgets for synchronized draft values."""

from uuid import uuid4

import pytest

from squid.core.errors import ValidationError
from squid.submissions.application import build_submission_manifest
from squid.submissions.domain import (
    CategoryForm,
    ControlKind,
    DraftChange,
    DraftSnapshot,
    FieldConstraints,
    FieldOperation,
    FieldOperationKind,
    FormField,
    FormManifest,
    FormSection,
    SubmissionOrigin,
    ValueKind,
)
from squid.submissions.domain.drafts import MAX_DRAFT_OPERATION_VALUE_BYTES, MAX_DRAFT_OPERATIONS


def operation(field_id: str, value: object) -> FieldOperation:
    return FieldOperation(
        operation_id=uuid4(),
        field_id=field_id,
        kind=FieldOperationKind.SET,
        value=value,  # type: ignore[arg-type]
    )


def test_operation_rejects_oversized_and_deep_json_before_copying() -> None:
    with pytest.raises(ValueError, match="byte limit"):
        operation("description", "x" * MAX_DRAFT_OPERATION_VALUE_BYTES)

    deeply_nested: object = "value"
    for _ in range(6):
        wrapped: list[object] = [deeply_nested]
        deeply_nested = wrapped
    with pytest.raises(ValueError, match="nested too deeply"):
        operation("description", deeply_nested)


def test_change_enforces_its_operation_count_in_the_domain() -> None:
    operations = tuple(operation(f"field_{index}", index) for index in range(MAX_DRAFT_OPERATIONS + 1))

    with pytest.raises(ValueError, match="cannot exceed"):
        DraftChange(
            base_revision=0,
            client_instance_id="browser:test",
            idempotency_key="bounded-change",
            operations=operations,
        )


def test_draft_rejects_cumulative_answer_growth_with_a_stable_domain_error() -> None:
    snapshot = DraftSnapshot(
        id=uuid4(),
        owner_account_id=1,
        schema_id="build_submission.v1",
        schema_revision=1,
        category="other",
    )
    answers = "x" * (MAX_DRAFT_OPERATION_VALUE_BYTES - 128)
    for revision in range(4):
        snapshot = snapshot.apply(
            DraftChange(
                base_revision=revision,
                client_instance_id="browser:test",
                idempotency_key=f"bounded-change-{revision}",
                operations=(operation(f"description_{revision}", answers),),
            )
        )

    with pytest.raises(ValidationError) as error:
        snapshot.apply(
            DraftChange(
                base_revision=4,
                client_instance_id="browser:test",
                idempotency_key="bounded-change-final",
                operations=(operation("description_final", answers),),
            )
        )

    assert error.value.public_context == {"reason": "answers_too_large"}


def test_string_list_constraints_bound_every_item() -> None:
    manifest = FormManifest(
        schema_id="bounded.v1",
        revision=1,
        minimum_protocol=1,
        maximum_protocol=1,
        common_sections=(
            FormSection(
                id="identity",
                title="Identity",
                fields=(
                    FormField(
                        id="creators",
                        label="Creators",
                        control=ControlKind.TEXT,
                        value_kind=ValueKind.STRING_LIST,
                        constraints=FieldConstraints(min_length=1, max_length=3, max_items=2),
                        repeatable=True,
                    ),
                ),
            ),
        ),
        categories=(CategoryForm(code="other", label="Other", sections=()),),
    )

    assert manifest.validate_answers(
        "other",
        {"creators": ["four"]},
        origin=SubmissionOrigin.WEB,
        require_complete=False,
    ) == {"creators": "too_long"}


def test_every_repeatable_manifest_value_has_an_item_length_limit() -> None:
    manifest = build_submission_manifest()

    for category in manifest.categories:
        for field in manifest.fields_for(category.code):
            if field.value_kind is ValueKind.STRING_LIST:
                assert field.constraints.max_length is not None, field.id

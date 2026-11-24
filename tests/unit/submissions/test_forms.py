"""Form manifests, partial validation and immutable revisioned draft operations."""

from collections.abc import Mapping
from dataclasses import replace
from uuid import UUID

import anyio
import pytest
from anyio.lowlevel import checkpoint

from squid.core.errors import JSONValue, ValidationError
from squid.core.submissions.application import (
    CURRENT_SUBMISSION_PROTOCOL,
    CheckedInFormManifestRegistry,
    FormOptionSet,
    SubmissionFormService,
    build_edit_manifest,
    build_submission_manifest,
)
from squid.core.submissions.domain import (
    ChoiceOption,
    ControlKind,
    DraftChange,
    DraftChangeKey,
    DraftRevisionConflictError,
    DraftSnapshot,
    DraftStatus,
    FieldOperation,
    FieldOperationKind,
    FieldRule,
    SubmissionAttentionReason,
    SubmissionOrigin,
    ValueKind,
)
from squid_ui.content.text import Localization


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
        "credits": [{"name": "Builder"}],
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
    source_version = next(field for field in manifest.fields_for("other") if field.id == "source_version")
    assert source_version.control is ControlKind.CHOICE
    assert source_version.option_source == "approved_source_versions"


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


def test_transition_uses_all_inputs_and_distinguishes_rules_on_the_same_field() -> None:
    def no_later_than_opening(value: JSONValue, related: Mapping[str, JSONValue]) -> bool:
        opening = related["opening_time"]
        assert type(value) is int
        assert type(opening) is int
        return value <= opening

    def no_later_than_closing(value: JSONValue, related: Mapping[str, JSONValue]) -> bool:
        closing = related["closing_time"]
        assert type(value) is int
        assert type(closing) is int
        return value <= closing

    manifest = build_edit_manifest()
    door = manifest.category("door")
    rules = (
        FieldRule("opening_limit", SubmissionAttentionReason.ABOVE_MAXIMUM, no_later_than_opening, ("opening_time",)),
        FieldRule("closing_limit", SubmissionAttentionReason.ABOVE_MAXIMUM, no_later_than_closing, ("closing_time",)),
    )
    door = replace(
        door,
        sections=tuple(
            replace(
                section,
                fields=tuple(
                    replace(field, rules=rules) if field.id == "visible_opening_time" else field
                    for field in section.fields
                ),
            )
            for section in door.sections
        ),
    )
    manifest = replace(manifest, categories=(door,))
    before: dict[str, JSONValue] = {"opening_time": 4, "visible_opening_time": 6, "closing_time": 8}
    origin = SubmissionOrigin.DISCORD

    violations = manifest.answer_issues("door", before | {"closing_time": 5}, origin=origin)
    assert [issue.model_dump(mode="json") for issue in violations] == [
        {
            "field": "visible_opening_time",
            "code": "rule_failed",
            "params": {"rule": rule, "reason": "above_maximum", "depends_on": [dependency]},
        }
        for rule, dependency in (("opening_limit", "opening_time"), ("closing_limit", "closing_time"))
    ]
    assert manifest.validate_answers("door", before, origin=origin) == {"visible_opening_time": "above_maximum"}
    assert manifest.validate_transition("door", before, before | {"display_name": "New name"}, origin=origin) == {}
    assert manifest.validate_transition("door", before, before | {"opening_time": 5}, origin=origin) == {
        "visible_opening_time": "above_maximum"
    }
    assert manifest.validate_transition("door", before, before | {"closing_time": 5}, origin=origin) == {
        "visible_opening_time": "above_maximum"
    }
    assert manifest.validate_transition("door", before, before | {"opening_time": 6}, origin=origin) == {}
    assert manifest.validate_answers("door", {"visible_opening_time": 6, "opening_time": "bad"}, origin=origin) == {
        "opening_time": "wrong_type"
    }


@pytest.mark.asyncio
async def test_checked_in_registry_retains_v2_and_serves_v3_vocabulary() -> None:
    registry = CheckedInFormManifestRegistry()

    current = await registry.current(locale="en")
    revision_two = await registry.get("build_submission.v1", 2, locale="en")
    revision_three = await registry.get("build_submission.v1", 3, locale="en")

    assert revision_two is not None
    assert revision_three is not None
    assert current.revision == 10
    old_context = next(section for section in revision_two.common_sections if section.id == "submission_context")
    new_context = next(section for section in revision_three.common_sections if section.id == "submission_context")
    assert (
        tuple(field.id for field in old_context.fields)
        == tuple(field.id for field in new_context.fields)
        == (
            "completion",
            "ai_generated",
            "sponsor_attribution",
        )
    )
    assert new_context.title == "About this submission"
    assert next(field for field in new_context.fields if field.id == "sponsor_attribution").label == (
        "Credit the Minecraft server this submission came from"
    )
    door = revision_three.category("door")
    geometry = next(section for section in door.sections if section.id == "door_geometry")
    assert [field.label for field in geometry.fields[:3]] == [
        "Clear opening width",
        "Clear opening height",
        "Clear opening depth",
    ]


@pytest.mark.asyncio
async def test_manifest_localization_is_isolated_between_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    def prefixed_localization(locale: str | None) -> Localization:
        prefix = locale or "default"
        return Localization(locale="fr", resources=(f"submissions-name-and-credits = {prefix}:Build identity",))

    monkeypatch.setattr("squid.core.submissions.application.forms.localization_for", prefixed_localization)
    titles: dict[str, str] = {}

    async def build_for(locale: str) -> None:
        await checkpoint()
        titles[locale] = build_submission_manifest(locale).common_sections[0].title

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(build_for, "alpha")
        tasks.start_soon(build_for, "beta")

    assert titles == {"alpha": "alpha:Build identity", "beta": "beta:Build identity"}


@pytest.mark.asyncio
async def test_dynamic_options_remain_category_aware() -> None:
    service = SubmissionFormService(FakeOptionCatalog(), CheckedInFormManifestRegistry())

    option_set = await service.options("approved_patterns", "door", locale="en")

    assert (await service.manifest(locale="en")).category("door").code == "door"
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
        schema_revision=2,
        category="other",
        answers={"display_name": "First"},
    )
    change = DraftChange(
        base_revision=0,
        client_instance_id="fabric:device-1",
        idempotency_key=DraftChangeKey("edit-0001"),
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
        schema_revision=2,
        category="other",
    ).transition(DraftStatus.PROCESSING)

    with pytest.raises(ValueError, match="cannot be edited"):
        draft.apply(
            DraftChange(
                base_revision=0,
                client_instance_id="web:browser-1",
                idempotency_key=DraftChangeKey("edit-0002"),
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


def test_revision_four_relaxes_sizes_and_hides_the_exported_version_on_discord() -> None:
    previous = build_submission_manifest(revision=3)
    current = build_submission_manifest(revision=4)
    answers = {"creators": ["Builder"], "schematic_visibility": "reviewer_only", "source_version": "Java 1.21"}
    door_answers = {**answers, "opening_width": 2, "opening_height": 3, "door_orientation": "door"}

    # Revision 3 still needs every size part and the exported version.
    assert set(previous.validate_answers("door", door_answers, origin=SubmissionOrigin.DISCORD)) == {
        "capture_width",
        "capture_height",
        "capture_depth",
        "opening_depth",
    }
    # Revision 4 accepts a door without depth or build size.
    assert current.validate_answers("door", door_answers, origin=SubmissionOrigin.DISCORD) == {}
    # Discord is not asked for the exported version, so a stored one is dropped; capture clients still need it.
    assert "source_version" not in current.visible_answers("door", door_answers, origin=SubmissionOrigin.DISCORD)
    capture = current.validate_answers(
        "other", {"creators": ["Builder"], "schematic_visibility": "reviewer_only"}, origin=SubmissionOrigin.FABRIC
    )
    assert capture == {"source_version": "required"}


def test_revision_six_adds_optional_media_links_without_changing_pinned_forms() -> None:
    old = {field.id for field in build_submission_manifest(revision=5).fields_for("other")}
    current = build_submission_manifest()
    assert {"image_urls", "video_urls", "world_download_urls"}.isdisjoint(old)
    fields = {field.id: field for field in current.fields_for("other")}
    for field_id in ("image_urls", "video_urls", "world_download_urls"):
        assert not fields[field_id].required
    assert (
        current.validate_answers(
            "other",
            {"image_urls": ["https://imgur.com/a/example"]},
            origin=SubmissionOrigin.DISCORD,
            require_complete=False,
        )
        == {}
    )
    assert current.validate_answers(
        "other", {"image_urls": ["javascript:alert(1)"]}, origin=SubmissionOrigin.DISCORD, require_complete=False
    ) == {"image_urls": "wrong_type"}


def test_revision_nine_takes_showcase_tags_with_values() -> None:
    previous = next(
        field for field in build_submission_manifest(revision=8).fields_for("other") if field.id == "showcase_tags"
    )
    current = build_submission_manifest(revision=9)
    field = next(field for field in current.fields_for("other") if field.id == "showcase_tags")

    assert (previous.control, previous.value_kind) == (ControlKind.MULTI_CHOICE, ValueKind.STRING_LIST)
    assert (field.control, field.value_kind) == (ControlKind.SHOWCASE_TAGS, ValueKind.SHOWCASE_TAG_LIST)
    assert (field.option_source, field.required_capability, field.constraints.max_items) == (
        "approved_showcase_tags",
        "showcase_tag_values",
        40,
    )

    def check(value: JSONValue) -> dict[str, str]:
        answers = {"showcase_tags": value}
        return current.validate_answers("other", answers, origin=SubmissionOrigin.WEB, require_complete=False)

    assert check([{"key": "compact"}, {"key": "speed", "value": "1.5"}]) == {}
    assert check(["compact"]) == {"showcase_tags": "wrong_type"}
    assert check([{"key": "compact"}, {"key": "compact"}]) == {"showcase_tags": "wrong_type"}
    assert check([{"key": f"tag_{index}"} for index in range(41)]) == {"showcase_tags": "too_many_items"}


def test_choice_options_name_a_known_value_type() -> None:
    assert ChoiceOption("speed", "Speed", "numeric").value_type == "numeric"
    with pytest.raises(ValidationError):
        ChoiceOption("speed", "Speed", "decimal")

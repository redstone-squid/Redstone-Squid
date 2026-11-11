"""Recalculation cannot invent missing facts or replace protected build metadata."""

import pytest

from squid.builds.domain import BuildCategory, BuildDraft, Status
from squid.builds.models import DoorBuild
from squid.core.errors import ValidationError
from squid.submissions.application.recalculation_values import RecalculationFacts, recalculation_values


def test_missing_inferred_dimensions_preserve_existing_values() -> None:
    build = DoorBuild(id=12, width=7, height=8, depth=9, door_width=2, door_height=3, door_depth=1)
    facts = RecalculationFacts.from_draft(BuildDraft(category=BuildCategory.DOOR, width=10, door_width=4))
    facts.patch(build).apply(build)
    assert build.dimensions == (10, 8, 9)
    assert build.door_dimensions == (4, 3, 1)


def test_recalculation_preserves_identity_owner_and_moderation_status() -> None:
    build = DoorBuild(id=12, submitter_account_id=7, submission_status=Status.CONFIRMED)
    RecalculationFacts(category=BuildCategory.DOOR, description="Updated explanation").patch(build).apply(build)
    assert (build.id, build.submitter_account_id, build.submission_status, build.category) == (
        12,
        7,
        Status.CONFIRMED,
        BuildCategory.DOOR,
    )
    assert build.description == "Updated explanation"
    assert "submission_status" not in recalculation_values(build)


def test_inferred_category_change_requires_a_different_target() -> None:
    with pytest.raises(ValidationError, match="category"):
        RecalculationFacts(category=BuildCategory.EXTENDER).patch(DoorBuild())


def test_recalculation_retains_original_inferred_note() -> None:
    facts = RecalculationFacts.from_draft(BuildDraft(description="Source explanation"))
    assert facts.description == "Source explanation"

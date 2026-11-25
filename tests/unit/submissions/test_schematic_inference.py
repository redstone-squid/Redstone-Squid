"""Schematic suggestions retain evidence without inventing semantic build facts."""

from dataclasses import replace
from uuid import uuid4

import pytest
from pytest_mock import MockerFixture

from squid.core.actors import ActingContext, Actor
from squid.core.errors import NotFoundError
from squid.core.schematics.domain.doors.detection import detect_cycle
from squid.core.schematics.infrastructure import wire
from squid.core.submissions.application.forms import CheckedInFormManifestRegistry, FormOptionSet, SubmissionFormService
from squid.core.submissions.application.schematic_inference import SchematicSubmissionInference
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.submissions.domain import ChoiceOption, SubmissionOrigin
from squid.core.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid.core.submissions.errors import DraftAccessDeniedError
from tests.unit.schematics.fakes import make_analysis
from tests.unit.submissions.test_api_contract import stored_draft


class Options:
    async def options(self, source: str, category: str, *, locale: str | None, context: ActingContext) -> FormOptionSet:
        values = (
            (ChoiceOption("source-java", "Java 1.21.0"),)
            if source == "approved_source_versions"
            else (ChoiceOption("no-slime", "Slimeless"), ChoiceOption("no-observers", "Observerless"))
        )
        return FormOptionSet(source, category, 1, values)


@pytest.fixture
def inference(mocker: MockerFixture) -> SchematicSubmissionInference:
    versions = mocker.Mock()
    versions.label_for_data_version = mocker.AsyncMock(return_value="Java 1.21.0")
    return SchematicSubmissionInference(SubmissionFormService(Options(), CheckedInFormManifestRegistry()), versions)


async def test_suggestions_preserve_player_answers_and_leave_semantic_fields_unknown(
    inference: SchematicSubmissionInference,
) -> None:
    draft = stored_draft()
    draft = replace(draft, snapshot=replace(draft.snapshot, schema_revision=8, answers={"capture_width": 99}))
    analysis = make_analysis()
    analysis = replace(
        analysis,
        metrics=replace(
            analysis.metrics,
            declared_name="My door",
            declared_author="Alice",
            source_data_version=100,
            block_counts={"minecraft:stone": 41, "minecraft:observer[facing=up]": 1},
        ),
    )
    result = await inference.suggest(
        draft, uuid4(), DraftSchematicState.READY, analysis, context=ActingContext.direct(Actor())
    )
    values = {suggestion.field_id: suggestion.value for suggestion in result.suggestions}
    assert values["capture_width"] == 3
    assert values["display_name"] == "My door"
    assert values["source_version"] == "source-java"
    assert values["restrictions"] == ["no-slime"]
    assert isinstance(values["credits"], list)
    assert values["credits"][0] == {
        "name": "Alice",
        "kind": "name",
        "username": None,
        "subject": None,
        "role": "creator",
        "attribution": "explicit",
        "source_message_id": None,
    }
    assert draft.snapshot.answers == {"capture_width": 99}
    assert all(detection.status == "not_detected" and detection.value is None for detection in result.detections)
    assert {"category", "opening_width", "door_orientation", "opening_time", "closing_time"} <= {
        detection.field_id for detection in result.detections
    }
    assert not {"version_compatibility", "opening_width", "rights_attestation"}.intersection(values)


async def test_old_analysis_does_not_claim_absent_components(inference: SchematicSubmissionInference) -> None:
    draft = stored_draft()
    draft = replace(draft, snapshot=replace(draft.snapshot, schema_revision=8))
    result = await inference.suggest(
        draft, uuid4(), DraftSchematicState.READY, make_analysis(), context=ActingContext.direct(Actor())
    )
    assert "restrictions" not in {suggestion.field_id for suggestion in result.suggestions}


async def test_untrusted_metadata_is_validated_and_source_version_respects_origin(
    inference: SchematicSubmissionInference,
) -> None:
    draft = stored_draft()
    draft = replace(draft, origin=SubmissionOrigin.DISCORD, snapshot=replace(draft.snapshot, schema_revision=8))
    analysis = make_analysis()
    analysis = replace(
        analysis,
        metrics=replace(
            analysis.metrics,
            declared_name="x" * 10000,
            declared_author="x" * 10000,
            source_data_version=100,
        ),
    )
    result = await inference.suggest(
        draft, uuid4(), DraftSchematicState.READY, analysis, context=ActingContext.direct(Actor())
    )
    assert {suggestion.field_id for suggestion in result.suggestions} == {
        "capture_width",
        "capture_height",
        "capture_depth",
    }


@pytest.mark.parametrize("state", [DraftSchematicState.PENDING, DraftSchematicState.REJECTED])
async def test_unready_uploads_offer_no_suggestions(
    inference: SchematicSubmissionInference,
    state: DraftSchematicState,
) -> None:
    result = await inference.suggest(
        stored_draft(), uuid4(), state, make_analysis(), context=ActingContext.direct(Actor())
    )
    assert not result.suggestions
    assert not result.detections
    assert result.measurements is None


async def test_draft_and_file_access_are_checked_before_returning_metadata(mocker: MockerFixture) -> None:
    draft = stored_draft()
    drafts = mocker.Mock()
    drafts.get_accessible_in = mocker.AsyncMock(return_value=draft)
    uow = mocker.MagicMock()
    uow.draft_schematics.list = mocker.AsyncMock(return_value=())
    uow.draft_schematics.analysis_for_draft = mocker.AsyncMock()
    scope = mocker.MagicMock()
    scope.__aenter__.return_value = uow
    service = DraftSchematicService(drafts, mocker.Mock(), uow_factory=lambda: scope)
    with pytest.raises(NotFoundError):
        await service.suggestions(draft.snapshot.id, uuid4(), context=ActingContext.for_actor(Actor(account_id=42)))
    uow.draft_schematics.analysis_for_draft.assert_not_called()
    source = DraftSchematic(uuid4(), "private.schem", "a" * 64, 1, DraftSchematicState.READY, discarded=True)
    uow.draft_schematics.list.return_value = (source,)
    with pytest.raises(NotFoundError):
        await service.suggestions(draft.snapshot.id, source.id, context=ActingContext.for_actor(Actor(account_id=42)))
    drafts.get_accessible_in.side_effect = DraftAccessDeniedError()
    with pytest.raises(DraftAccessDeniedError):
        await service.suggestions(draft.snapshot.id, source.id, context=ActingContext.for_actor(Actor(account_id=43)))
    uow.draft_schematics.analysis_for_draft.assert_not_called()


async def test_inference_reports_shared_dimensions_with_ambiguous_block_placements(
    inference: SchematicSubmissionInference,
) -> None:
    opened = {(x, y, z): "stone" for z in (0, 1) for x, y in ((-1, 0), (1, 0), (0, -1), (0, 1))}
    closed = opened | {(0, 0, 0): "glass", (0, 0, 1): "glass"}
    door = detect_cycle(opened, closed, opened, {"stone", "glass"})
    analysis = replace(make_analysis(), door=door)
    restored = wire.decode_analysis(wire.encode_analysis(analysis))
    result = await inference.suggest(
        stored_draft(), uuid4(), DraftSchematicState.READY, restored, context=ActingContext.direct(Actor())
    )
    assert result.door == door
    assert door.candidates[0].disputed_blocks == ((0, 0, 0), (0, 0, 1))
    detected = {d.field_id: d.value for d in result.detections if d.status == "detected"}
    assert detected == {"door_orientation": "door", "opening_width": 1, "opening_height": 1}
    assert any(d.field_id == "opening_depth" and d.status == "ambiguous" for d in result.detections)

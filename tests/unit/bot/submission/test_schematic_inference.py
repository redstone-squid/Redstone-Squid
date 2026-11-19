"""The /build submit workspace can infer empty fields from its main schematic."""

from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from squid.core.actors import Actor
from squid.core.submissions.application.intake import SuppliedAttachment
from squid.core.submissions.application.schematic_inference import (
    SchematicFieldSuggestion,
    SchematicSubmissionSuggestions,
)
from squid.core.submissions.application.schematics import DraftSchematicService
from squid.core.submissions.domain.intake import IntakeStatus
from squid.core.submissions.domain.schematics import DraftSchematic, DraftSchematicState
from squid_ui.runtime import transaction
from squid_ui.testing import labels
from tests.unit.bot.submission.test_draft_editor import Intake, open_editor, setup


async def test_submit_panel_infers_missing_values_without_overwriting_command_options() -> None:
    services, drafts = setup("door")
    drafts.current = replace(
        drafts.current, snapshot=replace(drafts.current.snapshot, answers={"display_name": "My title"})
    )
    source_id = uuid4()
    cast(Intake, services.submission_intake).files = (
        SuppliedAttachment(source_id, "door.schem", "schematic", IntakeStatus.READY),
    )
    schematics = Mock(spec=DraftSchematicService)
    schematics.list = AsyncMock(
        return_value=(DraftSchematic(source_id, "door.schem", "a" * 64, 1, DraftSchematicState.READY, primary=True),)
    )
    schematics.suggestions = AsyncMock(
        return_value=SchematicSubmissionSuggestions(
            source_id,
            DraftSchematicState.READY,
            0,
            suggestions=(
                SchematicFieldSuggestion("capture_width", 5, "Measured bounds"),
                SchematicFieldSuggestion("display_name", "Metadata title", "Embedded title"),
            ),
        )
    )
    services = replace(services, submission_schematics=schematics)
    editor = await open_editor(services, drafts)
    assert "Infer from schematic" in labels(editor.render())
    with transaction() as tx:
        await editor.workspace.infer_schematic(tx)
    assert drafts.current.snapshot.answers["display_name"] == "My title"
    assert drafts.current.snapshot.answers["capture_width"] == 5
    assert "opening_width" not in drafts.current.snapshot.answers
    assert len(editor.workspace.edits.undoable) == 1
    schematics.suggestions.assert_awaited_once_with(drafts.current.snapshot.id, Actor(account_id=7), source_id)


@pytest.mark.parametrize("state", [DraftSchematicState.PENDING, DraftSchematicState.REJECTED])
async def test_unready_schematic_does_not_change_discord_answers(state: DraftSchematicState) -> None:
    services, drafts = setup()
    source_id = uuid4()
    schematics = Mock(spec=DraftSchematicService)
    schematics.list = AsyncMock(
        return_value=(DraftSchematic(source_id, "door.schem", "a" * 64, 1, state, primary=True),)
    )
    schematics.suggestions = AsyncMock(return_value=SchematicSubmissionSuggestions(source_id, state, 0))
    services = replace(services, submission_schematics=schematics)
    editor = await open_editor(services, drafts)
    with transaction() as tx:
        await editor.workspace.infer_schematic(tx)
    assert drafts.current.snapshot.answers == {}
    assert editor.workspace.notice
    assert not editor.workspace.edits.undoable


async def test_discord_suggestions_cannot_overwrite_an_intervening_edit() -> None:
    services, drafts = setup()
    source_id = uuid4()
    schematics = Mock(spec=DraftSchematicService)
    schematics.list = AsyncMock(
        return_value=(DraftSchematic(source_id, "door.schem", "a" * 64, 1, DraftSchematicState.READY),)
    )

    async def suggestions(*_args: object) -> SchematicSubmissionSuggestions:
        drafts.current = replace(
            drafts.current, snapshot=replace(drafts.current.snapshot, revision=1, answers={"display_name": "New title"})
        )
        return SchematicSubmissionSuggestions(
            source_id,
            DraftSchematicState.READY,
            0,
            suggestions=(SchematicFieldSuggestion("display_name", "Old title", "Embedded title"),),
        )

    schematics.suggestions = AsyncMock(side_effect=suggestions)
    editor = await open_editor(replace(services, submission_schematics=schematics), drafts)
    with transaction() as tx:
        await editor.workspace.infer_schematic(tx)
    assert drafts.current.snapshot.answers == {"display_name": "New title"}
    assert not editor.workspace.edits.undoable
    assert editor.workspace.notice

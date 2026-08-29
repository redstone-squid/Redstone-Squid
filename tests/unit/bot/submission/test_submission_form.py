"""Semantic submission workspace tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.views import SubmissionOutcome, SubmissionScreen, _submission_basics_form
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft, DoorBuild
from squid_ui_discord.sessions import Reject
from squid_ui_discord.testing import commit_render
from tests.helpers.discord import invocation_scope, make_interaction, make_layout_bot


def _component(**kwargs: Any) -> SubmissionScreen:
    return SubmissionScreen(BuildDraft(**kwargs), cast(BuildService, object()), on_submit=AsyncMock())


def test_submission_form_uses_semantic_controls() -> None:
    component = _component(door_orientation="Door", door_width=2, door_height=2)

    nodes = component.render()

    assert isinstance(nodes[0], sl.semantic.Section)
    assert any(isinstance(node, sl.semantic.Choices) for node in nodes)
    assert any(isinstance(node, sl.semantic.ActionControls) for node in nodes)


def test_submission_screen_rejects_a_second_live_draft() -> None:
    assert SubmissionScreen.session_name == "build-submission"
    assert SubmissionScreen.timeout == 300
    assert isinstance(SubmissionScreen.admission.collision, Reject)


async def test_basics_form_describes_portable_fields() -> None:
    interaction = make_interaction().interaction
    async with invocation_scope(interaction) as invocation:
        form = _submission_basics_form(BuildDraft(), invocation)

    assert form.field_keys == ("door_size", "pattern", "dimensions", "versions", "creators")
    first = form.items[0]
    assert isinstance(first, sl.forms.FormField)
    assert first.label == "Door opening size"


async def test_changing_the_door_type_marks_the_message_root_dirty() -> None:
    component = _component()
    bot = make_layout_bot()
    message_root = bot.client_runtime.mount(component, access=sd.Everyone(), timeout=300)
    commit_render(message_root)

    await component._door_changed(cast(sl.ChoiceEvent, SimpleNamespace(selected=("Door",))))

    assert message_root.pending is True
    assert component.build.door_orientation == "Door"


async def test_cancel_closes_the_workspace() -> None:
    component = _component()
    event = SimpleNamespace(finish=AsyncMock())

    await component._cancel(cast(sl.PressEvent, event))

    assert component.cancelled is True
    event.finish.assert_awaited_once()


async def test_submission_persists_only_once_after_duplicate_finish() -> None:
    outcome = SubmissionOutcome(DoorBuild(id=7), sl.status("submitted"))
    persist = AsyncMock(return_value=outcome)
    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        cast(BuildService, object()),
        on_submit=persist,
    )
    event = SimpleNamespace(acknowledge=AsyncMock(), finish=AsyncMock(), notice=AsyncMock())

    await component._submit(cast(sl.PressEvent, event))
    await component._submit(cast(sl.PressEvent, event))

    persist.assert_awaited_once()
    event.finish.assert_awaited_once()
    event.notice.assert_awaited_once()

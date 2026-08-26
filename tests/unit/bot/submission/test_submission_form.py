"""Semantic submission workspace tests."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import squid_ui as sl
from squid.bot.submission.ui.views import SubmissionFormComponent, _submission_basics_form
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft
from squid_ui_discord.testing import commit_render
from tests.helpers.discord import make_layout_bot


def _component(**kwargs: Any) -> SubmissionFormComponent:
    return SubmissionFormComponent(BuildDraft(**kwargs), cast(BuildService, object()))


def test_submission_form_uses_semantic_controls() -> None:
    component = _component(door_orientation="Door", door_width=2, door_height=2)

    nodes = component.render()

    assert isinstance(nodes[0], sl.semantic.Section)
    assert any(isinstance(node, sl.semantic.Choices) for node in nodes)
    assert any(isinstance(node, sl.semantic.ActionControls) for node in nodes)


def test_basics_form_describes_portable_fields() -> None:
    form = _submission_basics_form(BuildDraft(), None)

    assert form.field_keys == ("door_size", "pattern", "dimensions", "versions", "creators")
    first = form.items[0]
    assert isinstance(first, sl.forms.FormField)
    assert first.label == "Door opening size"


async def test_changing_the_door_type_marks_the_message_root_dirty() -> None:
    component = _component()
    message_root = component.mount(source=make_layout_bot())
    commit_render(message_root)

    await component._door_changed(cast(sl.ChoiceEvent, SimpleNamespace(selected=("Door",))))

    assert message_root.pending is True
    assert component.build.door_orientation == "Door"


async def test_cancel_closes_the_workspace() -> None:
    component = _component()
    event = SimpleNamespace(finish=AsyncMock())

    await component._cancel(cast(sl.PressEvent, event))

    assert component.value is False
    event.finish.assert_awaited_once()

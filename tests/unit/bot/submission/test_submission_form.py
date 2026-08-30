"""Semantic submission workspace tests."""

from typing import Any

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.ui.views import SubmissionOutcome, SubmissionScreen, _submission_basics_form
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft, DoorBuild
from squid_ui.testing import RecordingResponder, choice_event, press, press_event
from squid_ui_discord.sessions import Reject
from squid_ui_discord.testing import commit_render
from tests.support.discord import make_layout_bot


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


async def _unused_submit() -> SubmissionOutcome:
    raise AssertionError("this test does not submit the draft")


def _component(**kwargs: Any) -> SubmissionScreen:
    return SubmissionScreen(BuildDraft(**kwargs), BuildRecorder(), on_submit=_unused_submit)


def test_submission_form_uses_semantic_controls() -> None:
    component = _component(door_orientation="Door", door_width=2, door_height=2)

    nodes = component.render()

    assert isinstance(nodes[0], sl.semantic.Section)
    assert any(isinstance(node, sl.semantic.Choices) for node in nodes)
    assert any(isinstance(node, sl.semantic.ActionControls) for node in nodes)


def test_submission_screen_rejects_a_second_live_draft() -> None:
    assert SubmissionScreen.session is not None
    assert SubmissionScreen.session.name == "build-submission"
    assert SubmissionScreen.timeout == 300
    assert isinstance(SubmissionScreen.session.admission.collision, Reject)


async def test_basics_form_describes_portable_fields() -> None:
    form = _submission_basics_form(BuildDraft())

    assert form.field_keys == ("door_size", "pattern", "dimensions", "versions", "creators")
    first = form.items[0]
    assert isinstance(first, sl.forms.FormField)
    assert first.label == "Door opening size"


async def test_changing_the_door_type_marks_the_message_root_dirty() -> None:
    component = _component()
    bot = make_layout_bot()
    message_root = bot.ui.mount(component, access=sd.Everyone(), timeout=300)
    commit_render(message_root)

    await component._door_changed(choice_event("Door"))

    assert message_root.pending is True
    assert component.build.door_orientation == "Door"


async def test_cancel_closes_the_workspace() -> None:
    component = _component()
    responder = RecordingResponder()

    await press(component, "cancel", responder=responder)

    assert component.cancelled is True
    assert responder.finished is True


async def test_submission_persists_only_once_after_duplicate_finish() -> None:
    outcome = SubmissionOutcome(DoorBuild(id=7), sl.status("submitted"))

    class SubmitRecorder:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> SubmissionOutcome:
            self.calls += 1
            return outcome

    persist = SubmitRecorder()
    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        BuildRecorder(),
        on_submit=persist,
    )
    responder = RecordingResponder()
    event = press_event(responder=responder)

    await component._submit(event)
    await component._submit(event)

    assert persist.calls == 1
    assert responder.finished is True
    assert len(responder.notices) == 1

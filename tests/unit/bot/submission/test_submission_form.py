"""Semantic submission workspace tests."""

from typing import Any, cast

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.input import format_invalid_values, invalid_web_urls, split_values
from squid.bot.submission.ui.views import (
    SubmissionOutcome,
    SubmissionScreen,
    _submission_basics_form,
    _submission_details_form,
)
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft, DoorBuild
from squid_ui.testing import RecordingResponder, choice_event, press, press_event
from squid_ui.text import Message
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


def test_submission_list_parsing_is_shared_and_discards_empty_entries() -> None:
    assert split_values(" Alice, , Bob ,, ") == ["Alice", "Bob"]


def test_web_url_validation_requires_an_absolute_http_url() -> None:
    assert invalid_web_urls(
        ("https://example.com/a", "http://example.test", "//example.com", "ftp://example.com", "https://")
    ) == ("//example.com", "ftp://example.com", "https://")


async def test_details_form_groups_actual_invalid_urls_by_field_and_preserves_attempt() -> None:
    form = _submission_details_form(BuildDraft())
    attempted = {
        "restrictions": "",
        "image_urls": "https://valid.example/image.png, invalid-image",
        "video_urls": "ftp://invalid-video, relative-video",
        "world_urls": "http://valid.example/world.zip",
        "notes": "keep this note",
    }

    result = await form.evaluate(attempted)

    assert result.values["notes"] == "keep this note"
    assert result.attempted == attempted
    field_errors = [error for error in result.errors if isinstance(error, sl.forms.FieldError)]
    assert all(isinstance(error.message, Message) for error in field_errors)
    messages = [cast(Message, error.message) for error in field_errors]
    assert [(error.key, message.template) for error, message in zip(field_errors, messages, strict=True)] == [
        ("image_urls", "Use complete `https://` or `http://` links. Invalid: {displayed}"),
        (
            "video_urls",
            "Use complete `https://` or `http://` links. Invalid: {displayed}",
        ),
    ]
    assert [message.params["displayed"] for message in messages] == [
        "`invalid-image`",
        "`ftp://invalid-video`, `relative-video`",
    ]


def test_invalid_url_rendering_is_deterministic_and_discord_safe() -> None:
    rendered = format_invalid_values(("x" * 600, "second"), maximum=100)

    assert len(rendered) == 100
    assert rendered.endswith("…")
    assert "`" in rendered


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

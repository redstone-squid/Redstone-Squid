"""Semantic submission workspace tests."""

from typing import Any

import discord
import pytest

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.submission.attachment_enrichment import AttachmentFailure, AttachmentLifecycle, primary_schematic
from squid.bot.submission.attachments import ClassifiedAttachment
from squid.bot.submission.input import format_invalid_values, invalid_web_urls, split_values
from squid.bot.submission.ui.views import (
    BASICS_FIELDS,
    DETAIL_FIELDS,
    SubmissionDeliveryError,
    SubmissionOutcome,
    SubmissionScreen,
    _submission_basics_form,
    _submission_details_form,
)
from squid.builds.application import BuildService
from squid.builds.domain import BuildDraft, BuildLink, DoorBuild
from squid.schematics.application import IngestedSchematic, IngestRequest
from squid_ui.testing import RecordingResponder, choice_event, press, press_event
from squid_ui_discord.modal import build_form_modal
from squid_ui_discord.sessions import Reject
from squid_ui_discord.testing import assert_within_limits, commit_render
from tests.support.discord import make_layout_bot
from tests.unit.schematics.fakes import make_analysis


class BuildRecorder(BuildService):
    def __init__(self) -> None:
        pass


async def _unused_submit(_attachments: tuple[AttachmentLifecycle, ...]) -> SubmissionOutcome:
    raise AssertionError("this test does not submit the draft")


def _component(**kwargs: Any) -> SubmissionScreen:
    return SubmissionScreen(BuildDraft(**kwargs), BuildRecorder(), on_submit=_unused_submit)


def _schematic(
    identity: str,
    filename: str,
    dimensions: tuple[int, int, int],
    *,
    failed: bool = False,
) -> AttachmentLifecycle:
    classified = ClassifiedAttachment("schematic", filename, "application/octet-stream")
    request = IngestRequest(data=identity.encode(), filename=filename)
    return AttachmentLifecycle(
        identity,
        filename,
        classification=classified,
        request=request,
        analysis=None if failed else IngestedSchematic(identity * 64, make_analysis(dimensions=dimensions)),
        failure=AttachmentFailure("analysis", "This schematic could not be analyzed.") if failed else None,
    )


def test_submission_form_uses_semantic_controls() -> None:
    component = _component(door_orientation="Door", door_width=2, door_height=2)

    nodes = component.render()

    assert isinstance(nodes[0], sl.semantic.Section)
    assert any(isinstance(node, sl.semantic.Choices) for node in nodes)
    assert any(isinstance(node, sl.semantic.ActionControls) for node in nodes)


def test_the_only_usable_schematic_defaults_and_failed_files_remain_visible() -> None:
    failed = _schematic("a", "broken.litematic", (1, 1, 1), failed=True)
    usable = _schematic("b", "working.litematic", (4, 5, 6))
    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        BuildRecorder(),
        attachments=(failed, usable),
        on_submit=_unused_submit,
    )

    selected = primary_schematic(component.attachments)
    assert selected is not None
    assert selected.identity == "b"
    assert component.build.dimensions == (4, 5, 6)
    assert component.is_ready
    rendered = str(component.render())
    assert "broken.litematic" in rendered
    assert "could not be analyzed" in rendered
    assert "working.litematic" in rendered


async def test_many_usable_schematics_require_an_explicit_primary_and_prefill_from_it() -> None:
    first = _schematic("a", "first.litematic", (3, 4, 5))
    second = _schematic("b", "second.litematic", (7, 8, 9))
    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        BuildRecorder(),
        attachments=(first, second),
        on_submit=_unused_submit,
    )

    assert component.requires_primary
    assert not component.is_ready
    assert component.build.dimensions == (None, None, None)
    assert "Choose which usable schematic is primary" in str(component.render())

    await component._primary_changed(choice_event("b"))

    selected = primary_schematic(component.attachments)
    assert selected is not None
    assert selected.identity == "b"
    assert component.build.dimensions == (7, 8, 9)
    assert component.is_ready


async def test_switching_primary_updates_only_an_untouched_prefill() -> None:
    first = _schematic("a", "first.litematic", (3, 4, 5))
    second = _schematic("b", "second.litematic", (7, 8, 9))
    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        BuildRecorder(),
        attachments=(first, second),
        on_submit=_unused_submit,
    )

    await component._primary_changed(choice_event("a"))
    await component._primary_changed(choice_event("b"))
    assert component.build.dimensions == (7, 8, 9)

    component.build.dimensions = (10, 11, 12)
    await component._primary_changed(choice_event("a"))
    assert component.build.dimensions == (10, 11, 12)


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
    assert [(error.key, str(error.message)) for error in field_errors] == [
        ("image_urls", "Use complete https:// or http:// links. Invalid: `invalid-image`"),
        (
            "video_urls",
            "Use complete https:// or http:// links. Invalid: `ftp://invalid-video`, `relative-video`",
        ),
    ]


@pytest.mark.parametrize("field", [*BASICS_FIELDS, *DETAIL_FIELDS], ids=lambda field: field.key)
def test_every_creation_field_round_trips_through_its_declared_parser_and_formatter(field: Any) -> None:
    draft = BuildDraft(
        door_width=2,
        door_height=3,
        patterns=["Regular", "Funnel"],
        width=4,
        height=5,
        depth=6,
        version_spec="1.21+",
        creators_ign=["Alice", "Bob"],
        wiring_placement_restrictions=["Seamless"],
        links=[
            BuildLink(url="https://example.com/image.png", media_type="image"),
            BuildLink(url="https://example.com/video.mp4", media_type="video"),
            BuildLink(url="https://example.com/world.zip", media_type="world-download"),
        ],
        extra_info={"user": "A note"},
    )
    value = field.draft_value(draft)

    assert field.parse(field.formatter(value)) == value


def test_creation_form_metadata_comes_from_the_same_specs_as_parsing() -> None:
    draft = BuildDraft()

    for fields, form in (
        (BASICS_FIELDS, _submission_basics_form(draft)),
        (DETAIL_FIELDS, _submission_details_form(draft)),
    ):
        controls = [item for item in form.items if isinstance(item, sl.forms.TextField)]
        assert [control.key for control in controls] == [field.key for field in fields]
        assert [control.label for control in controls] == [field.label for field in fields]
        assert [control.maximum for control in controls] == [field.maximum for field in fields]


@pytest.mark.parametrize("form_factory", [_submission_basics_form, _submission_details_form])
def test_creation_forms_fit_the_strict_discord_modal_boundary(form_factory: Any) -> None:
    async def submit(_interaction: discord.Interaction, _values: dict[str, object]) -> None:
        pass

    modal = build_form_modal(form_factory(BuildDraft()), on_submit=submit, strict=True)

    assert_within_limits(modal)


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

        async def __call__(self, _attachments: tuple[AttachmentLifecycle, ...]) -> SubmissionOutcome:
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


async def test_post_persistence_failure_never_claims_nothing_was_saved() -> None:
    build = DoorBuild(id=7)

    async def fail_delivery(_attachments: tuple[AttachmentLifecycle, ...]) -> SubmissionOutcome:
        outcome = SubmissionOutcome(build, sl.status("delivery pending"), delivery_complete=False)
        raise SubmissionDeliveryError(outcome)

    component = SubmissionScreen(
        BuildDraft(door_orientation="Door", door_width=2, door_height=2),
        BuildRecorder(),
        on_submit=fail_delivery,
    )

    with pytest.raises(SubmissionDeliveryError):
        await component._submit(press_event(responder=RecordingResponder()))

    rendered = str(component.render())
    assert component.outcome is not None
    assert component.outcome.build is build
    assert "was saved" in rendered
    assert "could not be delivered" in rendered
    assert "nothing was saved" not in rendered

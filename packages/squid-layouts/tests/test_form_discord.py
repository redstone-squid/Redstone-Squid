"""Discord form presentation, submission funnel, and validation retry."""

from typing import cast
from unittest.mock import AsyncMock, Mock

import discord
import pytest

import squid_layouts as sl
from squid_layouts.discord import Everyone, Mount
from squid_layouts.discord.modal import CheckboxGroupField
from squid_layouts.discord.modals import EntityField, EntityType, FileField, build_form_modal
from squid_layouts.discord.testing import commit_render, fake_interaction
from squid_layouts.forms import FormText


async def _ignore_raw(interaction, values) -> None: ...


def _component(modal: discord.ui.Modal) -> discord.ui.Item:
    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    return label.component


def test_form_items_preserve_static_text_order() -> None:
    spec = sl.forms.FormSpec(
        "Ordered",
        (FormText("Before"), sl.forms.TextField(key="name", label="Name"), FormText("After")),
    )

    modal = build_form_modal(spec, on_submit=_ignore_raw)

    assert isinstance(modal.children[0], discord.ui.TextDisplay)
    assert isinstance(modal.children[1], discord.ui.Label)
    assert isinstance(modal.children[2], discord.ui.TextDisplay)


def test_checkbox_group_uses_native_component_and_typed_declaration_order() -> None:
    fallback = sl.forms.MultiChoiceField(
        options=(sl.forms.ChoiceOption("a", "A", 1), sl.forms.ChoiceOption("b", "B", 2)),
        required=False,
    )
    field = CheckboxGroupField(
        key="checks",
        label="Checks",
        options=fallback.options,
        required=False,
        fallback=fallback,
    )

    modal = build_form_modal(sl.forms.FormSpec("Checks", (field,)), on_submit=_ignore_raw)

    assert isinstance(_component(modal), discord.ui.CheckboxGroup)
    assert field.parse(["b", "a"]) == (1, 2)
    assert field.parse([]) == ()


@pytest.mark.parametrize(
    ("field", "component_type"),
    [
        (sl.forms.TextField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.TextAreaField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.IntField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.FloatField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.DurationField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.DateField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.TimeField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.DateTimeField(key="value", label="Value"), discord.ui.TextInput),
        (sl.forms.ZonedDateTimeField(key="value", label="Value"), discord.ui.TextInput),
        (
            sl.forms.ChoiceField(
                key="value",
                label="Value",
                options=(sl.forms.ChoiceOption("a", "A", "a"), sl.forms.ChoiceOption("b", "B", "b")),
            ),
            discord.ui.RadioGroup,
        ),
        (
            sl.forms.MultiChoiceField(
                key="value",
                label="Value",
                options=(sl.forms.ChoiceOption("a", "A", "a"), sl.forms.ChoiceOption("b", "B", "b")),
            ),
            discord.ui.Select,
        ),
        (sl.forms.BoolField(key="value", label="Value"), discord.ui.Checkbox),
        (EntityField(key="value", label="Value", entity_type=EntityType.ROLE), discord.ui.RoleSelect),
        (FileField(key="value", label="Value"), discord.ui.FileUpload),
    ],
)
def test_portable_and_discord_fields_build_native_modal_components(field, component_type) -> None:
    modal = build_form_modal(sl.forms.FormSpec("Fields", (field,)), on_submit=_ignore_raw)

    assert isinstance(_component(modal), component_type)


def test_zoned_datetime_field_exposes_configured_timezone_as_description_fallback() -> None:
    field = sl.forms.ZonedDateTimeField(key="value", label="Value", timezone="America/New_York")

    modal = build_form_modal(sl.forms.FormSpec("Zoned", (field,)), on_submit=_ignore_raw)

    label = modal.children[0]
    assert isinstance(label, discord.ui.Label)
    assert label.description == "America/New_York"


@pytest.mark.parametrize(
    ("component", "wire_type"),
    [
        (discord.ui.Select(options=[discord.SelectOption(label="One", value="one")]), 3),
        (discord.ui.UserSelect(), 5),
        (discord.ui.RoleSelect(), 6),
        (discord.ui.MentionableSelect(), 7),
        (discord.ui.ChannelSelect(), 8),
        (discord.ui.FileUpload(), 19),
        (
            discord.ui.RadioGroup(
                options=[
                    discord.RadioGroupOption(label="One", value="one"),
                    discord.RadioGroupOption(label="Two", value="two"),
                ]
            ),
            21,
        ),
        (
            discord.ui.CheckboxGroup(
                options=[
                    discord.CheckboxGroupOption(label="One", value="one"),
                    discord.CheckboxGroupOption(label="Two", value="two"),
                ]
            ),
            22,
        ),
    ],
)
def test_discordpy_modal_component_inventory_serializes_through_labels(
    component: discord.ui.Item,
    wire_type: int,
) -> None:
    modal = discord.ui.Modal(title="Inventory")
    modal.add_item(discord.ui.Label(text="Field", component=component))

    label = modal.to_dict()["components"][0]

    assert label["type"] == 18
    assert label["component"]["type"] == wire_type


def test_modal_budget_rejects_implicit_chunking() -> None:
    spec = sl.forms.FormSpec(
        "Too large",
        tuple(sl.forms.TextField(key=f"field-{index}", label=f"Field {index}") for index in range(6)),
    )

    with pytest.raises(sl.errors.LayoutInvariantError, match="1-5"):
        build_form_modal(spec, on_submit=_ignore_raw)


def test_multi_choice_builds_select_cardinality_and_defaults() -> None:
    field = sl.forms.MultiChoiceField(
        key="values",
        label="Values",
        required=False,
        options=tuple(sl.forms.ChoiceOption(str(index), f"Choice {index}", index) for index in range(4)),
        minimum=1,
        maximum=3,
    )
    modal = build_form_modal(sl.forms.FormSpec("Many", (field,), prefill={"values": (1, "3")}), on_submit=_ignore_raw)

    component = _component(modal)
    assert isinstance(component, discord.ui.Select)
    assert component.min_values == 1
    assert component.max_values == 3
    assert [option.value for option in component.options if option.default] == ["1", "3"]


def test_multi_choice_rejects_more_than_twenty_five_options() -> None:
    field = sl.forms.MultiChoiceField(
        key="values",
        label="Values",
        options=tuple(sl.forms.ChoiceOption(str(index), f"Choice {index}", index) for index in range(26)),
    )

    with pytest.raises(sl.errors.LayoutInvariantError, match="1-25"):
        build_form_modal(sl.forms.FormSpec("Many", (field,)), on_submit=_ignore_raw)


async def test_file_reader_wraps_discord_attachments_in_portable_values() -> None:
    submitted: dict[str, object] = {}

    async def capture(_interaction, values: dict[str, object]) -> None:
        submitted.update(values)

    modal = build_form_modal(sl.forms.FormSpec("Upload", (FileField(key="file", label="File"),)), on_submit=capture)
    component = _component(modal)
    assert isinstance(component, discord.ui.FileUpload)
    attachment = Mock(spec=discord.Attachment)
    attachment.filename = "build.litematic"
    attachment.content_type = "application/octet-stream"
    attachment.size = 42
    attachment.url = "https://cdn.example.invalid/build.litematic"
    attachment.read = AsyncMock(return_value=b"schematic")
    component._values = [attachment]  # pyrefly: ignore[missing-attribute]

    await modal.on_submit(fake_interaction())

    uploaded = cast(tuple[sl.forms.UploadedFile, ...], submitted["file"])[0]
    assert (uploaded.name, uploaded.media_type, uploaded.size, uploaded.url) == (
        "build.litematic",
        "application/octet-stream",
        42,
        "https://cdn.example.invalid/build.litematic",
    )
    assert await uploaded.read() == b"schematic"


def test_file_field_rejects_more_than_ten_uploads() -> None:
    with pytest.raises(sl.errors.LayoutInvariantError, match="0-10"):
        build_form_modal(
            sl.forms.FormSpec("Upload", (FileField(key="file", label="File", maximum=11),)),
            on_submit=_ignore_raw,
        )


class DurationPanel(sl.Component):
    seconds: int = sl.state(0)

    def __init__(
        self, *, validation_policy: sl.forms.FormValidationPolicy = sl.forms.FormValidationPolicy.RETRY
    ) -> None:
        self.events: list[sl.SubmitEvent] = []
        self.spec = sl.forms.FormSpec(
            "Duration",
            (sl.forms.DurationField(key="duration", label="Duration"),),
            validation_policy=validation_policy,
        )

    def render(self) -> sl.LayoutNode:
        return sl.form("Duration", self.spec, key="duration", on_submit=self.submitted)

    async def submitted(self, event: sl.SubmitEvent) -> None:
        self.events.append(event)
        if not event.errors:
            self.seconds = cast(int, event.values["duration"])


def _text_input(modal: discord.ui.Modal) -> discord.ui.TextInput:
    component = _component(modal)
    assert isinstance(component, discord.ui.TextInput)
    return component


async def _open_form(panel: DurationPanel, mount: Mount) -> discord.ui.Modal:
    interaction = fake_interaction()
    await mount.dispatch("duration", interaction)
    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, discord.ui.Modal)
    return modal


async def test_invalid_submission_preserves_input_for_framework_retry() -> None:
    panel = DurationPanel()
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)
    modal = await _open_form(panel, mount)
    _text_input(modal)._value = "eventually"  # pyrefly: ignore[missing-attribute]

    submission = fake_interaction()
    await modal.on_submit(submission)

    assert panel.events == []
    retry_view = submission.response.send_message.await_args.kwargs["view"]
    texts = [item.content for item in retry_view.walk_children() if isinstance(item, discord.ui.TextDisplay)]
    assert any("Duration" in text and "30m" in text for text in texts)
    retry = next(item for item in retry_view.walk_children() if isinstance(item, discord.ui.Button))

    retry_interaction = fake_interaction()
    await retry.callback(retry_interaction)
    retried = retry_interaction.response.send_modal.await_args.args[0]
    assert _text_input(retried).default == "eventually"


async def test_valid_submission_dispatches_typed_event_and_commits_a_new_generation() -> None:
    panel = DurationPanel()
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)
    generation = mount.generation
    modal = await _open_form(panel, mount)
    _text_input(modal)._value = "2h"  # pyrefly: ignore[missing-attribute]

    await modal.on_submit(fake_interaction())

    assert panel.seconds == 7200
    assert len(panel.events) == 1
    assert isinstance(panel.events[0], sl.SubmitEvent)
    assert panel.events[0].values == {"duration": 7200}
    assert mount.generation > generation


async def test_exclusive_submission_from_a_stale_generation_is_ignored() -> None:
    panel = DurationPanel()
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)
    modal = await _open_form(panel, mount)
    _text_input(modal)._value = "2h"  # pyrefly: ignore[missing-attribute]
    panel.seconds = 60
    commit_render(mount)

    submission = fake_interaction()
    await modal.on_submit(submission)

    assert panel.seconds == 60
    assert panel.events == []
    submission.response.defer.assert_awaited_once()


async def test_accept_and_mark_delivers_parse_errors_to_the_handler() -> None:
    panel = DurationPanel(validation_policy=sl.forms.FormValidationPolicy.ACCEPT_AND_MARK)
    mount = Mount(panel, access=Everyone(), timeout=None)
    commit_render(mount)
    modal = await _open_form(panel, mount)
    _text_input(modal)._value = "bad"  # pyrefly: ignore[missing-attribute]

    await modal.on_submit(fake_interaction())

    assert len(panel.events) == 1
    assert panel.events[0].errors == (sl.forms.FieldError("duration", "Enter a duration such as 30m, 12h, or 7d."),)


def test_scale_field_renders_a_radio_group_within_the_radio_span() -> None:
    field = sl.forms.ScaleField(key="score", label="Score", labels={1: "Poor", 5: "Excellent"})
    modal = build_form_modal(sl.forms.FormSpec("Rate", (field,), prefill={"score": 4}), on_submit=_ignore_raw)

    component = _component(modal)
    assert isinstance(component, discord.ui.RadioGroup)
    assert [option.value for option in component.options] == ["1", "2", "3", "4", "5"]
    assert [option.label for option in component.options] == ["Poor", "2", "3", "4", "Excellent"]
    assert [option.value for option in component.options if option.default] == ["4"]


def test_a_wide_scale_falls_back_to_a_parsed_text_input() -> None:
    field = sl.forms.ScaleField(key="score", label="Score", minimum=0, maximum=100)
    modal = build_form_modal(sl.forms.FormSpec("Rate", (field,), prefill={"score": 42}), on_submit=_ignore_raw)

    component = _component(modal)
    assert isinstance(component, discord.ui.TextInput)
    assert component.default == "42"
    assert component.placeholder == "0\N{EN DASH}100"
    assert field.parse("42") == 42

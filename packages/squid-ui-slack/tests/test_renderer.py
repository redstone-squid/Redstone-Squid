"""Slack SDK drawing for message, modal, and App Home scenes."""

from dataclasses import replace
from datetime import date
from typing import Any, cast

import pytest
from slack_sdk.models.blocks import (
    ActionsBlock,
    ButtonElement,
    CardBlock,
    CarouselBlock,
    CheckboxesElement,
    ConversationSelectElement,
    DatePickerElement,
    HeaderBlock,
    InputBlock,
    NumberInputElement,
    PlainTextInputElement,
    RadioButtonsElement,
    SectionBlock,
    TableBlock,
    UserSelectElement,
)
from slack_sdk.models.views import View

import squid_ui as sl
import squid_ui_slack as ss
from squid_ui import forms, scene
from squid_ui.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.planning.adapter import AdapterCapability


async def _pressed(_event: ActionEvent) -> None: ...


async def _submitted(_event: SubmitEvent) -> None: ...


async def _entities_changed(_event: sl.EntityEvent) -> None: ...


def test_message_renderer_draws_sdk_blocks_and_client_kwargs() -> None:
    result = sl.planning.plan(
        sl.stack(
            sl.heading("Build review"),
            sl.paragraph("Ready for *review*."),
            sl.action_controls(
                sl.action_control("Approve", _pressed, key="approve", tone=sl.Tone.SUCCESS),
                sl.link("Source", "https://example.com/build", key="source"),
                key="actions",
            ),
        ),
        target=ss.SLACK_MESSAGE_SDK343,
    )

    payload = ss.MessageRenderer().draw(result.scene, plan=result)

    assert isinstance(payload.blocks[0], HeaderBlock)
    assert isinstance(payload.blocks[1], SectionBlock)
    actions = cast(ActionsBlock, payload.blocks[2])
    approve = cast(ButtonElement, actions.elements[0])
    source = cast(ButtonElement, actions.elements[1])
    assert approve.action_id == "approve"
    assert approve.style == "primary"
    assert source.action_id is not None
    assert source.action_id.startswith("squid:url:")
    assert payload.to_kwargs() == {"text": payload.text, "blocks": list(payload.blocks)}
    assert [block.to_dict()["type"] for block in payload.blocks] == ["header", "section", "actions"]


def test_message_renderer_resolves_stored_and_injected_asset_urls() -> None:
    stored = sl.document.Asset(
        "report",
        "report.txt",
        "text/plain",
        sl.document.StoredAsset("https://example.com/report.txt"),
    )
    result = sl.planning.plan(
        sl.download("Report", stored, key="report-download"),
        target=ss.SLACK_MESSAGE_SDK343,
    )

    stored_payload = ss.MessageRenderer().draw(result.scene, plan=result)
    stored_button = cast(ButtonElement, cast(ActionsBlock, stored_payload.blocks[0]).elements[0])
    assert stored_button.url == "https://example.com/report.txt"

    resolved_payload = ss.MessageRenderer(asset_resolver=lambda asset: f"https://cdn.example.com/{asset.name}").draw(
        result.scene
    )
    resolved_button = cast(ButtonElement, cast(ActionsBlock, resolved_payload.blocks[0]).elements[0])
    assert resolved_button.url == "https://cdn.example.com/report.txt"


def test_modal_renderer_maps_every_portable_field_family() -> None:
    spec = forms.FormSpec(
        "Edit build",
        (
            forms.TextField("Name", "name", maximum=80),
            forms.IntField("Ticks", "ticks", minimum=1, maximum=20),
            forms.DateField("Built", "built", default=date(2026, 8, 29)),
            forms.BoolField("Published", "published", default=True),
            forms.ChoiceField(
                "State",
                "state",
                options=(
                    forms.ChoiceOption("draft", "Draft", "draft"),
                    forms.ChoiceOption("ready", "Ready", "ready"),
                ),
            ),
        ),
    )
    result = sl.planning.plan(
        sl.form("Save", spec, key="edit-build", on_submit=_submitted),
        target=ss.SLACK_MODAL_SDK343,
    )

    view = ss.ModalRenderer().draw(result.scene, plan=result)

    assert isinstance(view, View)
    assert view.type == "modal"
    assert view.callback_id == "edit-build"
    assert all(isinstance(block, InputBlock) for block in view.blocks)
    input_blocks = cast(list[InputBlock], view.blocks)
    assert [type(block.element) for block in input_blocks] == [
        PlainTextInputElement,
        NumberInputElement,
        DatePickerElement,
        CheckboxesElement,
        RadioButtonsElement,
    ]
    assert view.to_dict()["submit"]["text"] == "Save"


def test_native_entity_selectors_draw_to_sdk_models() -> None:
    user = sl.entities(
        key="reviewer",
        entity_type=EntityType.USER,
        selection=sl.controlled((EntityRef(EntityKind.USER, "U123"),), _entities_changed),
    )
    conversation = sl.entities(
        key="conversation",
        entity_type=EntityType.CONVERSATION,
        conversation_types=(ConversationType.WORKSPACE_PUBLIC, ConversationType.WORKSPACE_PRIVATE),
    )
    result = sl.planning.plan(sl.stack(user, conversation), target=ss.SLACK_HOME_SDK343)

    view = ss.HomeRenderer().draw(result.scene, plan=result)
    elements = [cast(ActionsBlock, block).elements[0] for block in view.blocks]

    assert isinstance(elements[0], UserSelectElement)
    assert elements[0].initial_user == "U123"
    assert isinstance(elements[1], ConversationSelectElement)
    assert elements[1].filter is not None
    assert elements[1].filter.include == ["public", "private"]


def test_home_renderer_draws_current_table_card_and_carousel_models() -> None:
    body = scene.SlackHomeView(
        (
            scene.SlackTable(((scene.SlackText("Build"), scene.SlackText("Status")),)),
            scene.SlackCard(title=scene.SlackText("Solo"), description=scene.SlackText("One card")),
            scene.SlackCarousel(
                (
                    scene.SlackCard(title=scene.SlackText("One")),
                    scene.SlackCard(title=scene.SlackText("Two")),
                )
            ),
        )
    )
    document = scene.Scene(scene.Codec.protocol, "slack.block-kit.home", 1, body)

    view = ss.HomeRenderer().draw(document)

    assert [type(block) for block in view.blocks] == [TableBlock, CardBlock, CarouselBlock]
    assert view.to_dict()["blocks"][0]["rows"][0][0] == {"type": "raw_text", "text": "Build"}


@pytest.mark.parametrize(
    ("changed", "match"),
    [
        ({"protocol": 99}, "scene protocol"),
        ({"target": "slack.block-kit.home"}, "cannot draw target"),
        ({"target_version": 2}, "target version"),
    ],
)
def test_renderer_rejects_the_wrong_scene_contract(changed: dict[str, Any], match: str) -> None:
    document = scene.Scene(scene.Codec.protocol, "slack.block-kit.message", 1, scene.SlackMessage("Hello"))

    with pytest.raises(sl.DrawInvariantError, match=match):
        ss.MessageRenderer().draw(replace(document, **changed))


def test_renderer_rejects_missing_capability_and_surface_violations() -> None:
    profile = ss.slack_sdk_adapter_profile(
        "limited-sdk",
        ">=3.43,<3.44",
        capabilities=frozenset({AdapterCapability.RENDER_SLACK_HOME}),
    )
    message = scene.Scene(scene.Codec.protocol, "slack.block-kit.message", 1, scene.SlackMessage("Hello"))
    invalid_home = scene.Scene(
        scene.Codec.protocol,
        "slack.block-kit.home",
        1,
        scene.SlackHomeView((scene.SlackInput("field", scene.SlackText("Field"), scene.SlackTextInput("field")),)),
    )

    with pytest.raises(sl.DrawInvariantError, match="lacks"):
        ss.MessageRenderer(adapter=profile).draw(message)
    with pytest.raises(sl.DrawInvariantError, match="cannot contain input"):
        ss.HomeRenderer().draw(invalid_home)


def test_message_renderer_refuses_unresolved_or_insecure_assets() -> None:
    metadata = scene.Asset("report", "report.txt", "text/plain")
    button = scene.SlackButton(
        scene.SlackText("Report", scene.SlackTextKind.PLAIN),
        asset=scene.SlackAssetRef("report", "report.txt", "text/plain"),
    )
    document = scene.Scene(
        scene.Codec.protocol,
        "slack.block-kit.message",
        1,
        scene.SlackMessage("Report", (scene.SlackActions((button,)),)),
        (metadata,),
    )

    with pytest.raises(sl.DrawInvariantError, match="public HTTPS URL"):
        ss.MessageRenderer().draw(document)
    with pytest.raises(sl.DrawInvariantError, match="public HTTPS URL"):
        ss.MessageRenderer(asset_resolver=lambda _asset: "http://example.com/report.txt").draw(document)


def test_sdk_validation_is_reported_as_a_draw_invariant() -> None:
    document = scene.Scene(
        scene.Codec.protocol,
        "slack.block-kit.message",
        1,
        scene.SlackMessage("Hello", (scene.SlackHeader(scene.SlackText("x" * 151, scene.SlackTextKind.PLAIN)),)),
    )

    with pytest.raises(sl.DrawInvariantError, match="header text"):
        ss.MessageRenderer().draw(document)

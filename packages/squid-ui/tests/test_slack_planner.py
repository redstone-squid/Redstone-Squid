"""Slack message, Home, and modal planning behavior."""

from datetime import UTC, date, datetime
from typing import Any, cast

import pytest

import squid_ui as sl
from squid_ui import forms, scene
from squid_ui.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.interactions import ActionEvent, SubmitEvent
from squid_ui.planning import PlanCache, PlanMemo, ResourceCost
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.resources import Axis
from squid_ui.runtime import PresentationState
from squid_ui.target_types import SlackAdapter

ADAPTER = AdapterProfile(SlackAdapter, "tests.slack", ">=3.43,<4")
MESSAGE = sl.slack.message_target(adapter=ADAPTER)
MODAL = sl.slack.modal_target(adapter=ADAPTER)
HOME = sl.slack.home_target(adapter=ADAPTER)


async def _pressed(_event: ActionEvent) -> None: ...


async def _submitted(_event: SubmitEvent) -> None: ...


async def _entities_changed(_event: sl.EntityEvent) -> None: ...


def test_message_compiles_text_actions_dates_and_accessible_fallback() -> None:
    instant = datetime(2026, 8, 29, 12, 30, tzinfo=UTC)
    result = sl.planning.plan(
        sl.stack(
            sl.heading("Build **review**"),
            sl.paragraph("See [docs](https://example.invalid/docs) & stay safe"),
            sl.timestamp(instant),
            sl.action_controls(
                sl.action_control("Approve", _pressed, key="approve", tone=sl.Tone.SUCCESS),
                sl.link("Source", "https://example.invalid/source", key="source"),
                key="actions",
            ),
        ),
        target=MESSAGE,
    )

    assert isinstance(result.scene.body, scene.SlackMessage)
    assert "Build review" in result.scene.body.text
    assert any(isinstance(block, scene.SlackHeader) for block in result.scene.body.blocks)
    assert any(
        isinstance(block, scene.SlackSection) and block.text is not None and "<!date^" in block.text.content
        for block in result.scene.body.blocks
    )
    actions = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackActions))
    assert [button.style for button in actions.elements if isinstance(button, scene.SlackButton)] == [
        scene.SlackButtonStyle.PRIMARY,
        scene.SlackButtonStyle.DEFAULT,
    ]
    assert set(result.bindings) == {"approve"}


def test_cards_are_planned_to_the_sdk_card_text_limits() -> None:
    result = sl.planning.plan(
        sl.article(sl.heading("T" * 200), sl.paragraph("B" * 300)),
        target=HOME,
    )

    card = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackCard))
    assert card.title is not None and len(card.title.content) == 150
    assert card.description is not None and len(card.description.content) == 200


def test_message_and_home_use_native_slack_entity_selectors() -> None:
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

    for target in (cast(Any, MESSAGE), cast(Any, HOME)):
        result = sl.planning.plan(sl.stack(user, conversation), target=target)
        selects = tuple(
            element
            for block in result.scene.body.blocks
            if isinstance(block, scene.SlackActions)
            for element in block.elements
            if isinstance(element, scene.SlackSelect)
        )

        assert [select.kind for select in selects] == [
            scene.SlackSelectKind.USERS,
            scene.SlackSelectKind.CONVERSATIONS,
        ]
        assert selects[0].initial_values == ("U123",)
        assert selects[1].conversation_types == (
            ConversationType.WORKSPACE_PUBLIC,
            ConversationType.WORKSPACE_PRIVATE,
        )


def test_unsupported_native_entity_family_requires_an_enumerated_fallback() -> None:
    with pytest.raises(sl.LayoutInvariantError, match="no native role selector"):
        sl.planning.plan(sl.entities(key="role", entity_type=EntityType.ROLE), target=MESSAGE)

    result = sl.planning.plan(
        sl.entities(
            sl.entity_choice(EntityRef(EntityKind.ROLE, "R123"), "Reviewer"),
            key="role",
            entity_type=EntityType.ROLE,
        ),
        target=MESSAGE,
    )

    actions = next(block for block in result.scene.body.blocks if isinstance(block, scene.SlackActions))
    assert isinstance(actions.elements[0], scene.SlackSelect)
    assert actions.elements[0].kind is scene.SlackSelectKind.STATIC


def test_message_form_is_a_modal_trigger_button_with_form_binding() -> None:
    spec = forms.FormSpec("Edit build", (forms.TextField("Name", "name"),))
    result = sl.planning.plan(sl.form("Edit", spec, key="edit", on_submit=_submitted), target=MESSAGE)

    assert len(result.scene.body.blocks) == 1
    assert isinstance(result.scene.body.blocks[0], scene.SlackActions)
    assert set(result.bindings) == {"edit"}
    assert set(result.form_bindings) == {"edit"}


def test_modal_requires_one_top_level_form_and_maps_portable_fields() -> None:
    spec = forms.FormSpec(
        "Edit build",
        (
            forms.FormText("All fields are reviewed."),
            forms.TextField("Name", "name", maximum=80),
            forms.TextAreaField("Notes", "notes"),
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
        sl.Document(
            (
                sl.paragraph("Review this build."),
                sl.form("Save", spec, key="edit-build", on_submit=_submitted),
            )
        ),
        target=MODAL,
    )

    body = result.scene.body
    assert isinstance(body, scene.SlackModalView)
    assert body.callback_id == "edit-build"
    assert body.title.content == "Edit build"
    assert body.submit.content == "Save"
    assert body.close.content == "Cancel"
    inputs = tuple(block for block in body.blocks if isinstance(block, scene.SlackInput))
    assert [type(block.element) for block in inputs] == [
        scene.SlackTextInput,
        scene.SlackTextInput,
        scene.SlackNumberInput,
        scene.SlackDatePicker,
        scene.SlackCheckboxes,
        scene.SlackRadioButtons,
    ]
    assert set(result.form_bindings) == {"edit-build"}
    assert not result.bindings


@pytest.mark.parametrize(
    "document",
    [
        sl.paragraph("No form"),
        sl.stack(
            sl.form(
                "Nested",
                forms.FormSpec("Nested", (forms.TextField("Name", "name"),)),
                key="nested",
                on_submit=_submitted,
            )
        ),
        sl.Document(
            (
                sl.form(
                    "One",
                    forms.FormSpec("One", (forms.TextField("Name", "name"),)),
                    key="one",
                    on_submit=_submitted,
                ),
                sl.form(
                    "Two",
                    forms.FormSpec("Two", (forms.TextField("Name", "name"),)),
                    key="two",
                    on_submit=_submitted,
                ),
            )
        ),
    ],
)
def test_modal_rejects_missing_nested_and_multiple_forms(document) -> None:
    with pytest.raises(sl.LayoutInvariantError, match=r"top-level sl\.form"):
        sl.planning.plan(document, target=MODAL)


def test_block_reservation_and_strict_degradation() -> None:
    document = sl.stack(*(sl.paragraph(f"Block {index}") for index in range(3)))
    result = sl.planning.plan(document, target=MESSAGE, reservation=ResourceCost({Axis.BLOCKS: 49}))

    assert len(result.scene.body.blocks) == 1
    assert any(event.severity is scene.PlanSeverity.DEGRADATION for event in result.report.events)
    with pytest.raises(sl.LayoutDegradedError, match="omitted"):
        sl.planning.plan(
            document,
            target=MESSAGE,
            reservation=ResourceCost({Axis.BLOCKS: 49}),
            strict=True,
        )


def test_slack_planner_reuses_exact_and_structural_caches() -> None:
    document = sl.paragraph("Cached")
    session = PresentationState()
    cache: PlanCache[scene.SlackMessage] = PlanCache()
    memo: PlanMemo[scene.SlackMessage] = PlanMemo()

    first = sl.planning.plan(document, target=MESSAGE, session=session, cache=cache, memo=memo)
    exact = sl.planning.plan(document, target=MESSAGE, session=session, cache=cache, memo=memo)
    structural = sl.planning.plan(sl.paragraph("Cached"), target=MESSAGE, session=session, cache=cache)

    assert first.metrics.reuse is scene.PlanReuse.MISS
    assert exact.metrics.reuse is scene.PlanReuse.EXACT
    assert structural.metrics.reuse is scene.PlanReuse.STRUCTURAL

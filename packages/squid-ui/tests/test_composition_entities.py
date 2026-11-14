"""Entity discovery preserves ownership, defaults and each target's native limits."""

from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

import squid_ui as sl
from squid_ui import scene
from squid_ui.entity import ConversationType, EntityKind, EntityRef, EntityType
from squid_ui.interactions import ActionResponder, Actor, EntitySelectionEvent, SelectionEvent
from squid_ui.planning import CompositionOptions, JointInfeasibleError, JointUnsupportedError, OccurrenceId, plan
from squid_ui.planning.adapter import AdapterProfile
from squid_ui.planning.discord import classic_target, components_v2_target
from squid_ui.planning.target import Target
from squid_ui.presentation import PresentationState
from squid_ui.target_types import DiscordAdapter, SlackAdapter

DISCORD = AdapterProfile(DiscordAdapter, "entities.discord", "1")
SLACK = AdapterProfile(SlackAdapter, "entities.slack", "1")
TARGETS: list[Target[Any, Any, Any, Any]] = [
    components_v2_target(DISCORD),
    classic_target(DISCORD),
    sl.html.target(),
    sl.slack.message_target(adapter=SLACK),
    sl.slack.home_target(adapter=SLACK),
]
ADA = EntityRef(EntityKind.USER, 1)
GRACE = EntityRef(EntityKind.USER, 2)


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_native_entity_picker_keeps_controlled_defaults_and_dispatch(target: Target[Any, Any, Any, Any]) -> None:
    changes: list[sl.EntityEvent] = []

    async def changed(event: sl.EntityEvent) -> None:
        changes.append(event)

    session = PresentationState()
    node = sl.region(
        sl.entities(key="people", entity_type=EntityType.USER, selection=sl.controlled((ADA,), changed)),
        key="r",
        title="R",
    )
    result = plan(node, target=target, session=session)
    assert result.allocation is not None
    assert not changes and not session.selections
    assert set(result.bindings) == {"people"}
    match result.scene.body:
        case scene.ComponentsV2(children=children):
            assert isinstance(children[0], scene.EntitySelect)
            assert children[0].default_values == (ADA,)
        case scene.ClassicMessage(rows=rows):
            assert isinstance(rows[0].controls[0], scene.EntitySelect)
            assert rows[0].controls[0].default_values == (ADA,)
        case scene.HtmlBody(children=children):
            control = children[0]
            assert isinstance(control, scene.HtmlElement)
            assert scene.HtmlAttribute(scene.HtmlAttributeName.VALUE, ADA.encode()) in control.attributes
        case scene.SlackMessage(blocks=blocks) | scene.SlackHomeView(blocks=blocks):
            assert isinstance(blocks[0], scene.SlackActions)
            control = blocks[0].elements[0]
            assert isinstance(control, scene.SlackSelect)
            assert control.kind == scene.SlackSelectKind.USERS
            assert control.initial_values == ("1",)
    await result.bindings["people"].handler(
        EntitySelectionEvent(Actor("reader"), Mock(spec=ActionResponder), values=(GRACE,), context=None)
    )
    assert len(changes) == 1
    assert (changes[0].selected, changes[0].added, changes[0].removed) == ((GRACE,), (GRACE,), (ADA,))
    assert not session.selections


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
async def test_uncontrolled_entity_changes_rebuild_delivered_output(target: Target[Any, Any, Any, Any]) -> None:
    session = PresentationState()
    node = sl.region(sl.entities(key="people", entity_type=EntityType.USER), key="r", title="R")
    first = plan(node, target=target, session=session)
    assert not session.selections
    session.apply_updates(first.session_updates)
    responder = Mock(spec=ActionResponder)
    responder.acknowledge = AsyncMock()
    await first.bindings["people"].handler(
        EntitySelectionEvent(Actor("reader"), responder, values=(ADA,), context=None)
    )
    responder.acknowledge.assert_awaited_once()
    responder.invalidate.assert_called_once()
    assert session.selection("people").selected == (ADA.encode(),)
    second = plan(node, target=target, session=session)
    assert second.allocation is not first.allocation
    assert second.scene != first.scene


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
async def test_slack_role_fallback_dispatches_typed_entities(target: Target[Any, Any, Any, Any]) -> None:
    role = EntityRef(EntityKind.ROLE, "reviewer")
    received: list[sl.EntityEvent] = []

    async def changed(event: sl.EntityEvent) -> None:
        received.append(event)

    node = sl.region(
        sl.entities(
            sl.entity_choice(role, "Reviewer"),
            key="roles",
            entity_type=EntityType.ROLE,
            selection=sl.controlled((), changed),
        ),
        key="r",
        title="R",
    )
    result = plan(node, target=target)
    block = result.scene.body.blocks[0]
    assert isinstance(block, scene.SlackActions)
    control = block.elements[0]
    assert isinstance(control, scene.SlackSelect)
    assert control.kind == scene.SlackSelectKind.STATIC
    await result.bindings["roles"].handler(
        SelectionEvent(Actor("reader"), Mock(spec=ActionResponder), values=(role.encode(),), context=None)
    )
    assert received[0].selected == (role,)


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
def test_slack_missing_native_family_has_a_specific_diagnostic(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(sl.entities(key="roles", entity_type=EntityType.ROLE), key="r", title="R")
    with pytest.raises(JointUnsupportedError) as caught:
        plan(node, target=target)
    assert "Slack role discovery" in str(caught.value.diagnostics.violations)


@pytest.mark.parametrize("target", [*TARGETS[:2], *TARGETS[3:]], ids=lambda target: target.id)
def test_native_entity_cardinality_obeys_reduced_limits(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(sl.entities(key="people", entity_type=EntityType.USER, maximum=2), key="r", title="R")
    limited = replace(
        target, limits=replace(target.limits, components=replace(target.limits.components, select_options=1))
    )
    with pytest.raises(JointInfeasibleError):
        plan(node, target=limited)


@pytest.mark.parametrize("target", TARGETS[3:], ids=lambda target: target.id)
def test_slack_conversation_filters_survive_shared_composition(target: Target[Any, Any, Any, Any]) -> None:
    filters = (ConversationType.WORKSPACE_PUBLIC, ConversationType.DIRECT)
    node = sl.region(
        sl.entities(key="rooms", entity_type=EntityType.CONVERSATION, conversation_types=filters), key="r", title="R"
    )
    result = plan(node, target=target)
    assert isinstance(result.scene.body.blocks[0], scene.SlackActions)
    selector = result.scene.body.blocks[0].elements[0]
    assert isinstance(selector, scene.SlackSelect)
    assert selector.kind == scene.SlackSelectKind.CONVERSATIONS
    assert selector.conversation_types == filters


@pytest.mark.parametrize("target", TARGETS, ids=lambda target: target.id)
def test_hidden_entity_picker_has_no_binding(target: Target[Any, Any, Any, Any]) -> None:
    node = sl.region(
        sl.paragraph("body", key="text"), sl.entities(key="people", entity_type=EntityType.USER), key="r", title="R"
    )
    region = OccurrenceId(("root", "r"))
    result = plan(node, target=target, composition=CompositionOptions(automatic=True, partitions={region: (1, 2)}))
    assert "people" not in result.bindings
    assert result.allocation is not None
    assert "people" in result.allocation.select({region: 1}).bindings

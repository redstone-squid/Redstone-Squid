"""Entity picker semantics, planning, and scene contracts."""

import pytest

import squid_ui as sl
import squid_ui_discord
from squid_ui import scene
from squid_ui.interactions import Actor, EntitySelectionEvent, Visibility
from squid_ui.planning import measure
from squid_ui.planning.limits import Axis
from squid_ui.primitives import EntitySelect
from squid_ui.runtime import PresentationState
from squid_ui_discord.testing import without_capabilities


async def _select(_event: sl.EntitySelectionEvent) -> None: ...


class _Responder:
    async def acknowledge(self) -> None: ...

    async def notice(self, text: sl.TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(
        self, form, *, key: str = "form", on_submit=None, mode=None, label="", record=None
    ) -> None: ...

    def invalidate(self) -> None: ...


def test_entity_reference_rejects_non_positive_ids() -> None:
    with pytest.raises(ValueError, match="positive"):
        sl.entity.EntityRef(sl.entity.EntityKind.USER, 0)


def test_entity_reference_round_trips_integer_and_string_ids_without_ambiguity() -> None:
    integer = sl.entity.EntityRef(sl.entity.EntityKind.USER, 42)
    string = sl.entity.EntityRef(sl.entity.EntityKind.USER, "42")

    assert sl.entity.decode_entity_ref(sl.entity.encode_entity_ref(integer)) == integer
    assert sl.entity.decode_entity_ref(sl.entity.encode_entity_ref(string)) == string


def test_entity_reference_rejects_legacy_presentation_state() -> None:
    with pytest.raises(ValueError, match="tag"):
        sl.entity.decode_entity_ref("user:42")


def test_picker_rejects_incompatible_defaults_and_channel_filters() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        EntitySelect(
            sl.entity.EntityType.USER,
            _select,
            "users",
            default_values=(sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 1),),
        )
    with pytest.raises(ValueError, match="conversation_types"):
        EntitySelect(
            sl.entity.EntityType.ROLE,
            _select,
            "roles",
            conversation_types=(sl.entity.ConversationType.GUILD_TEXT,),
        )


def test_entity_select_costs_an_action_row_and_control() -> None:
    solved = measure([EntitySelect(sl.entity.EntityType.USER, _select, "users")])

    assert solved.cost.get(Axis.COMPONENTS) == 2
    assert solved.cost.get(Axis.ROWS) == 1
    assert solved.cost.get(Axis.CONTROLS) == 1


def test_native_semantic_picker_lowers_to_entity_scene() -> None:
    plan = sl.planning.plan(
        sl.entities(key="users", entity_type=sl.entity.EntityType.USER), target=squid_ui_discord.DISCORD_V2_DPY27
    )

    assert isinstance(plan.scene.components_v2.children[0], scene.EntitySelect)


async def test_managed_native_entity_selection_survives_a_second_render() -> None:
    session = PresentationState()
    node = sl.entities(
        key="moderator",
        entity_type=sl.entity.EntityType.USER,
        selection=sl.uncontrolled(()),
    )
    first = sl.planning.plan(node, target=squid_ui_discord.DISCORD_V2_DPY27, session=session)
    handler = first.bindings["moderator"].handler
    await handler(
        EntitySelectionEvent(
            Actor("7"),
            _Responder(),
            values=(sl.entity.EntityRef(sl.entity.EntityKind.USER, 123456),),
        )
    )

    second = sl.planning.plan(node, target=squid_ui_discord.DISCORD_V2_DPY27, session=session)

    assert isinstance(second.scene.components_v2.children[0], scene.EntitySelect)
    assert second.scene.components_v2.children[0].default_values == (
        sl.entity.EntityRef(sl.entity.EntityKind.USER, 123456),
    )


def test_semantic_picker_uses_enumerated_fallback_without_capability() -> None:
    target = without_capabilities(squid_ui_discord.DISCORD_V2_DPY27, "actions.entity")
    plan = sl.planning.plan(
        sl.entities(
            sl.entity_choice(sl.entity.EntityRef(sl.entity.EntityKind.USER, 1), "Ada"),
            key="users",
            entity_type=sl.entity.EntityType.USER,
        ),
        target=target,
    )

    assert isinstance(plan.scene.components_v2.children[0], scene.Select)


def test_fallback_entity_picker_drops_unenumerated_managed_selection() -> None:
    target = without_capabilities(squid_ui_discord.DISCORD_V2_DPY27, "actions.entity")
    session = PresentationState()
    session.select(
        "users",
        (sl.entity.encode_entity_ref(sl.entity.EntityRef(sl.entity.EntityKind.USER, 999)),),
    )
    node = sl.entities(
        sl.entity_choice(sl.entity.EntityRef(sl.entity.EntityKind.USER, 1), "Ada"),
        key="users",
        entity_type=sl.entity.EntityType.USER,
        selection=sl.uncontrolled(()),
    )

    plan = sl.planning.plan(node, target=target, session=session)

    assert isinstance(plan.scene.components_v2.children[0], scene.Select)
    assert all(not option.default for option in plan.scene.components_v2.children[0].options)


def test_semantic_picker_refuses_without_native_capability_or_fallback() -> None:
    target = without_capabilities(squid_ui_discord.DISCORD_V2_DPY27, "actions.entity")

    with pytest.raises(sl.errors.LayoutInvariantError, match="enumerated fallback"):
        sl.planning.plan(sl.entities(key="users", entity_type=sl.entity.EntityType.USER), target=target)


def test_entity_scene_round_trips_mixed_mentionable_defaults() -> None:
    node = scene.EntitySelect(
        sl.entity.EntityType.MENTIONABLE,
        "mentions",
        default_values=(
            sl.entity.EntityRef(sl.entity.EntityKind.USER, 1),
            sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 2),
        ),
    )
    plan = sl.planning.plan(
        EntitySelect(
            node.entity_type,
            _select,
            node.action,
            default_values=node.default_values,
            max_values=2,
        ),
        target=squid_ui_discord.DISCORD_V2_DPY27,
    )

    assert scene.Codec.loads(scene.Codec.dumps(plan.scene)) == plan.scene


def test_entity_scene_round_trips_string_ids_and_conversation_filters() -> None:
    document = scene.Scene(
        scene.Codec.protocol,
        "discord.components-v2",
        1,
        scene.ComponentsV2(
            (
                scene.EntitySelect(
                    sl.entity.EntityType.CONVERSATION,
                    "conversation",
                    default_values=(sl.entity.EntityRef(sl.entity.EntityKind.CONVERSATION, "C012345"),),
                    conversation_types=(sl.entity.ConversationType.WORKSPACE_PUBLIC,),
                ),
            )
        ),
    )

    assert scene.Codec.loads(scene.Codec.dumps(document)) == document

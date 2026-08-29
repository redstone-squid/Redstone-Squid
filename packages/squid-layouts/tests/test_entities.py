"""Entity picker semantics, planning, and scene contracts."""

from dataclasses import replace

import pytest

import squid_layouts as sl
from squid_layouts.planning import measure
from squid_layouts.primitives import EntitySelect
from squid_layouts.scene import Codec, SceneEntitySelect, SceneSelect


async def _select(_event: sl.EntitySelectionEvent) -> None: ...


def test_entity_reference_rejects_non_positive_ids() -> None:
    with pytest.raises(ValueError, match="positive"):
        sl.entity.EntityRef(sl.entity.EntityKind.USER, 0)


def test_picker_rejects_incompatible_defaults_and_channel_filters() -> None:
    with pytest.raises(ValueError, match="incompatible"):
        EntitySelect(
            sl.entity.EntityType.USER,
            _select,
            "users",
            default_values=(sl.entity.EntityRef(sl.entity.EntityKind.ROLE, 1),),
        )
    with pytest.raises(ValueError, match="channel_types"):
        EntitySelect(sl.entity.EntityType.ROLE, _select, "roles", channel_types=(sl.entity.ChannelType.TEXT,))


def test_entity_select_costs_an_action_row_and_control() -> None:
    solved = measure([EntitySelect(sl.entity.EntityType.USER, _select, "users")])

    assert solved.cost.get("components") == 2
    assert solved.cost.get("rows") == 1
    assert solved.cost.get("controls") == 1


def test_native_semantic_picker_lowers_to_entity_scene() -> None:
    plan = sl.planning.plan(
        sl.entities(key="users", entity_type=sl.entity.EntityType.USER), target=sl.discord.V2_TARGET
    )

    assert isinstance(plan.scene.components_v2.children[0], SceneEntitySelect)


def test_semantic_picker_uses_enumerated_fallback_without_capability() -> None:
    target = replace(sl.discord.V2_TARGET, capabilities=sl.discord.V2_TARGET.capabilities - {"actions.discord.entity"})
    plan = sl.planning.plan(
        sl.entities(
            sl.entity_choice(sl.entity.EntityRef(sl.entity.EntityKind.USER, 1), "Ada"),
            key="users",
            entity_type=sl.entity.EntityType.USER,
        ),
        target=target,
    )

    assert isinstance(plan.scene.components_v2.children[0], SceneSelect)


def test_semantic_picker_refuses_without_native_capability_or_fallback() -> None:
    target = replace(sl.discord.V2_TARGET, capabilities=sl.discord.V2_TARGET.capabilities - {"actions.discord.entity"})

    with pytest.raises(sl.errors.LayoutInvariantError, match="enumerated fallback"):
        sl.planning.plan(sl.entities(key="users", entity_type=sl.entity.EntityType.USER), target=target)


def test_entity_scene_round_trips_mixed_mentionable_defaults() -> None:
    node = SceneEntitySelect(
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
        target=sl.discord.V2_TARGET,
    )

    assert Codec.loads(Codec.dumps(plan.scene)) == plan.scene

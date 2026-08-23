"""Entity picker semantics, planning, and scene contracts."""

from dataclasses import replace

import pytest

import squid_layouts as sl
from squid_layouts.interactions import Actor, EntitySelectionEvent, Visibility
from squid_layouts.planning import measure
from squid_layouts.primitives import EntitySelect
from squid_layouts.runtime import PresentationSession
from squid_layouts.scene import Codec, SceneEntitySelect, SceneSelect


async def _select(_event: sl.EntitySelectionEvent) -> None: ...


class _Responder:
    async def acknowledge(self) -> None: ...

    async def notice(self, text: sl.TextLike, *, visibility: Visibility = Visibility.PRIVATE) -> None: ...

    async def redirect(self, url: str) -> None: ...

    async def finish(self) -> None: ...

    async def present_form(self, form, *, key: str = "form", on_submit=None, policy=None) -> None: ...

    def invalidate(self) -> None: ...


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


async def test_managed_native_entity_selection_survives_a_second_render() -> None:
    session = PresentationSession()
    node = sl.entities(
        key="moderator",
        entity_type=sl.entity.EntityType.USER,
        selection=sl.managed(()),
    )
    first = sl.planning.plan(node, target=sl.discord.V2_TARGET, session=session)
    handler = first.bindings["moderator"].handler
    await handler(
        EntitySelectionEvent(
            Actor("7"),
            _Responder(),
            values=(sl.entity.EntityRef(sl.entity.EntityKind.USER, 123456),),
        )
    )

    second = sl.planning.plan(node, target=sl.discord.V2_TARGET, session=session)

    assert isinstance(second.scene.components_v2.children[0], SceneEntitySelect)
    assert second.scene.components_v2.children[0].default_values == (
        sl.entity.EntityRef(sl.entity.EntityKind.USER, 123456),
    )


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


def test_fallback_entity_picker_drops_unenumerated_managed_selection() -> None:
    target = replace(sl.discord.V2_TARGET, capabilities=sl.discord.V2_TARGET.capabilities - {"actions.discord.entity"})
    session = PresentationSession()
    session.select("users", ("user:999",))
    node = sl.entities(
        sl.entity_choice(sl.entity.EntityRef(sl.entity.EntityKind.USER, 1), "Ada"),
        key="users",
        entity_type=sl.entity.EntityType.USER,
        selection=sl.managed(()),
    )

    plan = sl.planning.plan(node, target=target, session=session)

    assert isinstance(plan.scene.components_v2.children[0], SceneSelect)
    assert all(not option.default for option in plan.scene.components_v2.children[0].options)


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

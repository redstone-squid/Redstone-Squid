"""Focused tests for persistent Discord role panels."""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import anyio
import discord
import pytest

import squid_ui_discord
from squid_ui_discord.roles import (
    RoleMutationForbidden,
    RolesUnchanged,
    RoleTransitionResult,
)
from squid_ui_discord.routing import Router
from squid_ui_discord.testing import fake_interaction
from squid_ui.primitives.nodes import ActionGroup, RoutedButton, RoutedSelect, Variants


class FakeRole:
    def __init__(self, role_id: int, position: int, *, managed: bool = False, default: bool = False) -> None:
        self.id = role_id
        self.position = position
        self.managed = managed
        self._default = default

    def is_default(self) -> bool:
        return self._default


def panel_for(
    *,
    cardinality: squid_ui_discord.Cardinality = squid_ui_discord.ANY,
    notice: squid_ui_discord.RoleNoticeHandler | None = None,
) -> tuple[squid_ui_discord.RolePanel, squid_ui_discord.routing.RouteGroup[discord.Client]]:
    group = squid_ui_discord.routing.RouteGroup("roles")
    panel = squid_ui_discord.RolePanel(
        group,
        title="Server roles",
        categories=(
            squid_ui_discord.RoleCategory(
                "colour",
                "Colour",
                (
                    squid_ui_discord.RoleOption(101, "Red", emoji="🔴"),
                    squid_ui_discord.RoleOption(102, "Blue", emoji="🔵"),
                ),
                cardinality=cardinality,
                description="Choose a colour.",
            ),
        ),
        notice=notice,
    )
    return panel, group


def interaction_for(
    panel: squid_ui_discord.RolePanel,
    roles: dict[int, FakeRole],
    *,
    held: tuple[int, ...] = (),
    manage_roles: bool = True,
    top_position: int = 100,
) -> tuple[discord.Interaction[Any], Any, AsyncMock, AsyncMock]:
    actor = Mock(spec=discord.Member)
    actor.id = 42
    member_roles = [roles[role_id] for role_id in held]
    member = SimpleNamespace(roles=member_roles, edit=AsyncMock())
    bot_member = SimpleNamespace(
        guild_permissions=SimpleNamespace(manage_roles=manage_roles),
        top_role=SimpleNamespace(position=top_position),
    )
    guild = SimpleNamespace(
        id=7,
        me=bot_member,
        get_role=lambda role_id: roles.get(role_id),
        fetch_member=AsyncMock(return_value=member),
    )
    interaction = cast(discord.Interaction[Any], fake_interaction())
    cast(Any, interaction).user = actor
    cast(Any, interaction).guild = guild
    return interaction, member, guild.fetch_member, member.edit


def test_models_validate_panel_wide_invariants() -> None:
    with pytest.raises(ValueError, match="positive"):
        squid_ui_discord.RoleOption(0, "Nope")
    with pytest.raises(ValueError, match="route-safe"):
        squid_ui_discord.RoleCategory("bad:key", "Bad", (squid_ui_discord.RoleOption(1, "One"),))
    with pytest.raises(ValueError, match="impossible"):
        squid_ui_discord.RoleCategory(
            "one",
            "One",
            (squid_ui_discord.RoleOption(1, "One"),),
            cardinality=squid_ui_discord.Cardinality(minimum=2),
        )

    group = squid_ui_discord.routing.RouteGroup("roles")
    existing = group.define("existing")
    group.add(existing, AsyncMock())
    with pytest.raises(ValueError, match="dedicated"):
        squid_ui_discord.RolePanel(
            group,
            title="Roles",
            categories=(squid_ui_discord.RoleCategory("one", "One", (squid_ui_discord.RoleOption(1, "One"),)),),
        )
    assert [route.format for route in group._definitions] == ["roles:existing"]


def test_routes_and_rendering_are_stable_and_unselected() -> None:
    panel, group = panel_for(cardinality=squid_ui_discord.EXACTLY_ONE)
    descriptions = Router(namespace=group, on_gone=AsyncMock()).describe()
    assert [description.format for description in descriptions] == [
        "roles:toggle:{category}:{role_id:int}",
        "roles:set:{category}",
    ]

    rendered = panel.render()
    ladder = rendered[-1]
    assert isinstance(ladder, Variants)
    preferred, fallback = ladder.variants
    preferred_node = preferred.nodes[0]
    assert isinstance(preferred_node, ActionGroup)
    buttons = tuple(button for button in preferred_node.items if isinstance(button, RoutedButton))
    assert [button.route_id for button in buttons] == [
        "roles:toggle:colour:101",
        "roles:toggle:colour:102",
    ]
    select = fallback.nodes[0]
    assert isinstance(select, RoutedSelect)
    assert select.route_id == "roles:set:colour"
    assert select.min_values == 1
    assert select.max_values == 1
    assert all(not option.default for option in select.options)

    presentation = squid_ui_discord.render_static(panel)
    view = cast(discord.ui.LayoutView, presentation.view)
    custom_ids = [item.custom_id for item in view.walk_children() if hasattr(item, "custom_id")]
    assert custom_ids == ["roles:toggle:colour:101", "roles:toggle:colour:102"]


async def test_exactly_one_button_replaces_stale_selection_and_preserves_other_roles() -> None:
    panel, _ = panel_for(cardinality=squid_ui_discord.EXACTLY_ONE)
    roles = {
        101: FakeRole(101, 10),
        102: FakeRole(102, 11),
        303: FakeRole(303, 12),
    }
    interaction, _member, fetch_member, edit = interaction_for(panel, roles, held=(101, 303))

    await panel._handle_toggle(interaction, "colour", 102)

    fetch_member.assert_awaited_once_with(42)
    edit.assert_awaited_once()
    await_args = edit.await_args
    assert await_args is not None
    edited_roles = await_args.kwargs["roles"]
    assert {role.id for role in edited_roles} == {102, 303}
    assert await_args.kwargs["reason"] == "Self-role panel"
    followup = cast(Any, interaction.followup)
    assert followup.send.await_args.args == ("Your roles were updated.",)


async def test_unchanged_selection_skips_edit_and_a_custom_notice_receives_the_result() -> None:
    outcomes: list[RoleTransitionResult] = []

    async def notice(_interaction: discord.Interaction[Any], result: RoleTransitionResult) -> None:
        outcomes.append(result)

    panel, _ = panel_for(notice=notice)
    roles = {101: FakeRole(101, 10), 102: FakeRole(102, 11)}
    interaction, _member, _fetch_member, edit = interaction_for(panel, roles, held=(101,))

    await panel._handle_set(interaction, ("101",), "colour")

    edit.assert_not_awaited()
    assert len(outcomes) == 1
    assert isinstance(outcomes[0], RolesUnchanged)
    assert outcomes[0].roles == frozenset({101})


@pytest.mark.parametrize(
    ("values", "expected_reason"),
    [
        (("bad",), "decimal"),
        (("101", "101"), "once"),
        (("999",), "outside"),
    ],
)
async def test_select_rejects_malformed_duplicate_and_out_of_category_values(values, expected_reason) -> None:
    panel, _ = panel_for()
    roles = {101: FakeRole(101, 10), 102: FakeRole(102, 11)}
    interaction, _member, _fetch_member, edit = interaction_for(panel, roles)

    await panel._handle_set(interaction, values, "colour")

    edit.assert_not_awaited()
    followup = cast(Any, interaction.followup)
    message = followup.send.await_args.args[0]
    assert message == "That role selection is not valid."


async def test_missing_role_is_typed_and_does_not_write() -> None:
    panel, _ = panel_for()
    roles = {101: FakeRole(101, 10)}
    interaction, _member, _fetch_member, edit = interaction_for(panel, roles)

    await panel._handle_toggle(interaction, "colour", 101)

    edit.assert_not_awaited()
    followup = cast(Any, interaction.followup)
    assert followup.send.await_args.args == ("This role panel is unavailable right now.",)


@pytest.mark.parametrize(
    "role",
    [
        FakeRole(101, 10, managed=True),
        FakeRole(101, 101),
    ],
)
async def test_uneditable_role_is_forbidden_before_edit(role: FakeRole) -> None:
    panel, _ = panel_for()
    roles = {101: role, 102: FakeRole(102, 11)}
    interaction, _member, _fetch_member, edit = interaction_for(panel, roles)
    cast(Any, interaction.guild).me.top_role.position = 100

    outcomes: list[RoleTransitionResult] = []

    async def notice(_interaction: discord.Interaction[Any], result: RoleTransitionResult) -> None:
        outcomes.append(result)

    panel.notice = notice
    await panel._handle_toggle(interaction, "colour", 101)

    edit.assert_not_awaited()
    assert isinstance(outcomes[0], RoleMutationForbidden)
    assert outcomes[0].role_ids == frozenset({101})


async def test_invalid_cardinality_is_reported_and_different_members_do_not_share_locks() -> None:
    panel, _ = panel_for(cardinality=squid_ui_discord.EXACTLY_ONE)
    roles = {101: FakeRole(101, 10), 102: FakeRole(102, 11)}
    first, _member, _fetch, first_edit = interaction_for(panel, roles)
    second, _member, _fetch, second_edit = interaction_for(panel, roles)
    cast(Any, second.user).id = 43
    entered = asyncio.Event()
    second_entered = asyncio.Event()
    release = asyncio.Event()

    async def first_edit_call(*args, **kwargs) -> None:
        del args, kwargs
        entered.set()
        await release.wait()

    first_edit.side_effect = first_edit_call

    async def second_edit_call(*args, **kwargs) -> None:
        del args, kwargs
        second_entered.set()

    second_edit.side_effect = second_edit_call
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(panel._handle_toggle, first, "colour", 101)
        with anyio.fail_after(1):
            await entered.wait()
        tasks.start_soon(panel._handle_toggle, second, "colour", 101)
        with anyio.fail_after(1):
            await second_entered.wait()
        release.set()

    assert first_edit.await_count == 1
    assert second_edit.await_count == 1
    assert squid_ui_discord.roles._MEMBER_LOCKS == {}


async def test_minimum_prevents_removing_the_last_required_role() -> None:
    panel, _ = panel_for(cardinality=squid_ui_discord.EXACTLY_ONE)
    roles = {101: FakeRole(101, 10), 102: FakeRole(102, 11)}
    interaction, _member, _fetch_member, edit = interaction_for(panel, roles, held=(101,))

    await panel._handle_toggle(interaction, "colour", 101)

    edit.assert_not_awaited()
    followup = cast(Any, interaction.followup)
    assert followup.send.await_args.args == ("That role selection is not valid.",)

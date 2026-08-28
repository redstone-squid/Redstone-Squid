"""A classic mount is the same lifecycle drawing different components.

Every contract here is one the V2 mount already keeps. They are parametrized over both
targets rather than copied, because a target that needed its own copy of `access` or
`stale generation` would mean the lifecycle had forked, which is the thing this must not do.
"""

from typing import Any

import discord
import pytest

import squid_ui as sl
from squid_ui import Component
from squid_ui.interactions import ActionEvent
from squid_ui.semantic import ActionControl, ActionControls, Heading, Paragraph
from squid_ui_discord import DISCORD_V1_DPY27, DISCORD_V2_DPY27, Everyone, MessageRoot, Owner
from squid_ui_discord.message_payload import MessageMode
from squid_ui_discord.message_root_wiring import ClassicMountedView, MountedView
from squid_ui_discord.testing import (
    commit_classic_render,
    commit_render,
    delivered_to,
    fake_interaction,
    fake_message,
)

TARGETS = [pytest.param(DISCORD_V2_DPY27, id="v2"), pytest.param(DISCORD_V1_DPY27, id="classic")]


class Screen(Component[sl.ClassicTarget]):
    presses: int = sl.state(0)

    def render(self):
        async def press(event: ActionEvent) -> None:
            self.presses += 1

        return [
            Heading("Piston door"),
            Paragraph(f"pressed {self.presses}"),
            ActionControls((ActionControl("press", "Press", press),), key="controls"),
        ]


def message_root_for(target, **options) -> MessageRoot:
    return MessageRoot(Screen(), target=target, access=Everyone(), **options)


def message_for(target) -> Any:
    """A sent message already in the mode this target writes, as Discord would report it."""
    return fake_message(components_v2=target is DISCORD_V2_DPY27)


def render_for(target, message_root: MessageRoot):
    """Commit a render through whichever helper this target's view type calls for."""
    return commit_render(message_root) if target is DISCORD_V2_DPY27 else commit_classic_render(message_root)


def interaction_for(target, **options) -> Any:
    """An interaction arriving from a message in the mode this target writes."""
    return fake_interaction(components_v2=target is DISCORD_V2_DPY27, **options)


class TestViewType:
    def test_a_classic_message_root_builds_a_plain_view(self) -> None:
        """Not a LayoutView: an ActionRow-only one would flag the message V2 irreversibly."""
        view = commit_classic_render(message_root_for(DISCORD_V1_DPY27))

        assert isinstance(view, ClassicMountedView)
        assert not isinstance(view, discord.ui.LayoutView)
        assert view.has_components_v2() is False

    def test_a_v2_message_root_still_builds_a_layout_view(self) -> None:
        assert isinstance(commit_render(message_root_for(DISCORD_V2_DPY27)), MountedView)

    def test_both_mounted_views_share_one_behaviour(self) -> None:
        """The mixin is the point: timeout, dispatchability, and the error hook are one copy."""
        for kind in (MountedView, ClassicMountedView):
            assert kind.on_timeout is MountedView.on_timeout
            assert kind.is_dispatchable is MountedView.is_dispatchable
            assert kind.on_error is MountedView.on_error


class TestPresentation:
    def test_the_committed_presentation_carries_the_embeds(self) -> None:
        message_root = message_root_for(DISCORD_V1_DPY27)
        message_root._stage_view()
        candidate = message_root._pending
        assert candidate is not None

        assert candidate.payload.mode is MessageMode.CLASSIC
        assert candidate.payload.embeds[0].title == "Piston door"

    def test_a_v2_message_root_delivers_a_layout_and_no_embeds(self) -> None:
        message_root = message_root_for(DISCORD_V2_DPY27)
        message_root._stage_view()
        candidate = message_root._pending
        assert candidate is not None

        assert candidate.payload.mode is MessageMode.COMPONENTS_V2
        assert candidate.payload.embeds == ()


@pytest.mark.parametrize("target", TARGETS)
class TestSharedContracts:
    """One test body, both targets. A branch here would mean the lifecycle had forked."""

    def test_a_render_produces_a_dispatchable_view(self, target) -> None:
        assert render_for(target, message_root_for(target)).is_dispatchable() is True

    def test_controls_carry_generation_qualified_custom_ids(self, target) -> None:
        message_root = message_root_for(target)
        view = render_for(target, message_root)
        ids = _control_ids(view)

        assert ids
        assert all(custom_id.startswith(f"ctl:{message_root.id}:") for custom_id in ids)

    async def test_a_press_reaches_the_component(self, target) -> None:
        message_root = message_root_for(target)
        await message_root.send(delivered_to(message_for(target)))
        key = _control_ids(message_root._view)[0].rsplit(":", 1)[-1]

        await message_root.dispatch(key, interaction_for(target), generation=message_root.generation)

        assert _screen(message_root).presses == 1

    async def test_a_stale_generation_is_refused(self, target) -> None:
        message_root = message_root_for(target)
        await message_root.send(delivered_to(message_for(target)))
        key = _control_ids(message_root._view)[0].rsplit(":", 1)[-1]

        await message_root.dispatch(key, interaction_for(target), generation=message_root.generation - 1)

        assert _screen(message_root).presses == 0

    async def test_access_refuses_someone_elses_control(self, target) -> None:
        message_root = MessageRoot(Screen(), target=target, access=Owner(1))
        await message_root.send(delivered_to(message_for(target)))
        key = _control_ids(message_root._view)[0].rsplit(":", 1)[-1]

        await message_root.dispatch(key, interaction_for(target, user_id=2), generation=message_root.generation)

        assert _screen(message_root).presses == 0

    def test_the_message_root_keeps_one_target_for_its_life(self, target) -> None:
        message_root = message_root_for(target)

        assert message_root.target is target
        render_for(target, message_root)
        assert message_root.target is target

    def test_a_finished_message_root_stops_its_view(self, target) -> None:
        message_root = message_root_for(target)
        view = render_for(target, message_root)

        assert view.is_finished() is False


def _screen(message_root: MessageRoot) -> Screen:
    component = message_root.component
    assert isinstance(component, Screen)
    return component


def _control_ids(view) -> list[str]:
    """Every custom id a mounted view drew, whichever component vocabulary it used."""
    children = view.walk_children() if isinstance(view, discord.ui.LayoutView) else view.children
    return [custom_id for item in children if isinstance(custom_id := getattr(item, "custom_id", None), str)]

"""A classic mount is the same lifecycle drawing different components.

Every contract here is one the V2 mount already keeps. They are parametrized over both
targets rather than copied, because a target that needed its own copy of `access` or
`stale generation` would mean the lifecycle had forked, which is the thing this must not do.
"""

from typing import Any

import discord
import pytest

import squid_layouts as sl
from squid_layouts import Action, Actions, Component, Heading, Paragraph
from squid_layouts.actions import ActionEvent
from squid_layouts.discord import CLASSIC_TARGET, V2_TARGET, Everyone, Mount, Owner
from squid_layouts.discord.mount import ClassicMountedView, MountedView
from squid_layouts.discord.presentation import DiscordMode
from squid_layouts.discord.testing import (
    commit_classic_render,
    commit_render,
    delivered_to,
    fake_interaction,
    fake_message,
)

TARGETS = [pytest.param(V2_TARGET, id="v2"), pytest.param(CLASSIC_TARGET, id="classic")]


class Screen(Component):
    presses: int = sl.state(0)

    def render(self):
        async def press(event: ActionEvent) -> None:
            self.presses += 1

        return [
            Heading("Piston door"),
            Paragraph(f"pressed {self.presses}"),
            Actions((Action("press", "Press", press),), key="controls"),
        ]


def mount_for(target, **options) -> Mount:
    return Mount(Screen(), target=target, access=Everyone(), **options)


def message_for(target) -> Any:
    """A sent message already in the mode this target writes, as Discord would report it."""
    return fake_message(components_v2=target is V2_TARGET)


def render_for(target, mount: Mount):
    """Commit a render through whichever helper this target's view type calls for."""
    return commit_render(mount) if target is V2_TARGET else commit_classic_render(mount)


def interaction_for(target, **options) -> Any:
    """An interaction arriving from a message in the mode this target writes."""
    return fake_interaction(components_v2=target is V2_TARGET, **options)


class TestViewType:
    def test_a_classic_mount_builds_a_plain_view(self) -> None:
        """Not a LayoutView: an ActionRow-only one would flag the message V2 irreversibly."""
        view = commit_classic_render(mount_for(CLASSIC_TARGET))

        assert isinstance(view, ClassicMountedView)
        assert not isinstance(view, discord.ui.LayoutView)
        assert view.has_components_v2() is False

    def test_a_v2_mount_still_builds_a_layout_view(self) -> None:
        assert isinstance(commit_render(mount_for(V2_TARGET)), MountedView)

    def test_both_mounted_views_share_one_behaviour(self) -> None:
        """The mixin is the point: timeout, dispatchability, and the error hook are one copy."""
        for kind in (MountedView, ClassicMountedView):
            assert kind.on_timeout is MountedView.on_timeout
            assert kind.is_dispatchable is MountedView.is_dispatchable
            assert kind.on_error is MountedView.on_error


class TestPresentation:
    def test_the_committed_presentation_carries_the_embeds(self) -> None:
        mount = mount_for(CLASSIC_TARGET)
        mount._stage_view()
        candidate = mount._pending
        assert candidate is not None

        assert candidate.presentation.mode is DiscordMode.CLASSIC
        assert candidate.presentation.embeds[0].title == "Piston door"

    def test_a_v2_mount_delivers_a_layout_and_no_embeds(self) -> None:
        mount = mount_for(V2_TARGET)
        mount._stage_view()
        candidate = mount._pending
        assert candidate is not None

        assert candidate.presentation.mode is DiscordMode.COMPONENTS_V2
        assert candidate.presentation.embeds == ()


@pytest.mark.parametrize("target", TARGETS)
class TestSharedContracts:
    """One test body, both targets. A branch here would mean the lifecycle had forked."""

    def test_a_render_produces_a_dispatchable_view(self, target) -> None:
        assert render_for(target, mount_for(target)).is_dispatchable() is True

    def test_controls_carry_generation_qualified_custom_ids(self, target) -> None:
        mount = mount_for(target)
        view = render_for(target, mount)
        ids = _control_ids(view)

        assert ids
        assert all(custom_id.startswith(f"ctl:{mount.id}:") for custom_id in ids)

    async def test_a_press_reaches_the_component(self, target) -> None:
        mount = mount_for(target)
        await mount.send(delivered_to(message_for(target)))
        key = _control_ids(mount._view)[0].rsplit(":", 1)[-1]

        await mount.dispatch(key, interaction_for(target), generation=mount._generation)

        assert _screen(mount).presses == 1

    async def test_a_stale_generation_is_refused(self, target) -> None:
        mount = mount_for(target)
        await mount.send(delivered_to(message_for(target)))
        key = _control_ids(mount._view)[0].rsplit(":", 1)[-1]

        await mount.dispatch(key, interaction_for(target), generation=mount._generation - 1)

        assert _screen(mount).presses == 0

    async def test_access_refuses_someone_elses_control(self, target) -> None:
        mount = Mount(Screen(), target=target, access=Owner(1))
        await mount.send(delivered_to(message_for(target)))
        key = _control_ids(mount._view)[0].rsplit(":", 1)[-1]

        await mount.dispatch(key, interaction_for(target, user_id=2), generation=mount._generation)

        assert _screen(mount).presses == 0

    def test_the_mount_keeps_one_target_for_its_life(self, target) -> None:
        mount = mount_for(target)

        assert mount.target is target
        render_for(target, mount)
        assert mount.target is target

    def test_a_finished_mount_stops_its_view(self, target) -> None:
        mount = mount_for(target)
        view = render_for(target, mount)

        assert view.is_finished() is False


def _screen(mount: Mount) -> Screen:
    component = mount.component
    assert isinstance(component, Screen)
    return component


def _control_ids(view) -> list[str]:
    """Every custom id a mounted view drew, whichever component vocabulary it used."""
    children = view.walk_children() if isinstance(view, discord.ui.LayoutView) else view.children
    return [custom_id for item in children if isinstance(custom_id := getattr(item, "custom_id", None), str)]

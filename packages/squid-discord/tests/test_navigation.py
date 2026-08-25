"""Navigator: stack navigation by composition."""

import discord

from squid_discord import Everyone, Mount
from squid_discord.navigation import Navigator
from squid_discord.testing import commit_render, fake_interaction
from squid_layouts import Component
from squid_layouts.primitives import Heading, Text


class Screen(Component):
    def __init__(self, name: str) -> None:
        self.name = name

    def render(self):
        return [Heading(self.name), Text(f"content of {self.name}")]


def _texts(view: discord.ui.LayoutView) -> list[str]:
    return [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]


def _labels(view: discord.ui.LayoutView) -> list[str | None]:
    return [b.label for b in view.walk_children() if isinstance(b, discord.ui.Button)]


async def test_push_pop_and_controls_render_last():
    navigator = Navigator(Screen("root"))
    mount = Mount(navigator, access=Everyone(), timeout=None)
    view = commit_render(mount)
    assert "## root" in _texts(view)
    assert _labels(view) == ["Back", "Close"]

    navigator.push(Screen("child"))
    interaction = fake_interaction()
    await mount.flush(interaction)
    pushed = interaction.response.edit_message.await_args.kwargs["view"]
    assert "## child" in _texts(pushed)

    await mount.dispatch("__nav_back", fake_interaction())
    assert navigator.current.name == "root"  # pyrefly: ignore


async def test_home_appears_only_when_deep():
    navigator = Navigator(Screen("root"))
    mount = Mount(navigator, access=Everyone(), timeout=None)
    navigator.push(Screen("a"))
    navigator.push(Screen("b"))
    view = commit_render(mount)
    assert "Home" in _labels(view)

    await mount.dispatch("__nav_home", fake_interaction())
    assert navigator.depth == 1


async def test_child_state_changes_rerender_through_the_shared_mount():
    from squid_layouts import state

    class Counting(Component):
        count: int = state(0)

        def render(self):
            return [Text(f"count {self.count}")]

    child = Counting()
    navigator = Navigator(Screen("root"))
    mount = Mount(navigator, access=Everyone(), timeout=None)
    commit_render(mount)
    navigator.push(child)
    commit_render(mount)

    child.count = 5

    assert mount._dirty
    assert "count 5" in _texts(commit_render(mount))


async def test_close_finishes_the_mount():
    navigator = Navigator(Screen("root"))
    mount = Mount(navigator, access=Everyone(), timeout=None)
    commit_render(mount)

    await mount.dispatch("__nav_close", fake_interaction())

    assert mount._finished

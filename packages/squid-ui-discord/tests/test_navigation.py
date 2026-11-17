"""StackNavigator: stack navigation by composition."""

from typing import override

from squid_ui import Component
from squid_ui.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_ui.ext.discord import Everyone, MessageRoot
from squid_ui.ext.discord import testing as sd
from squid_ui.ext.discord.navigation import StackNavigator
from squid_ui.ext.discord.primitives import Heading, Text
from squid_ui.ext.discord.target_types import ComponentsV2Target
from squid_ui.ext.discord.testing import commit_render, interaction_harness
from squid_ui.runtime import ComponentRuntime
from squid_ui.selectors import index_tree, query, select


class Screen(Component[ComponentsV2Target]):
    def __init__(self, name: str) -> None:
        self.name = name

    @override
    def render(self):
        return [Heading(self.name), Text(f"content of {self.name}")]


async def test_push_pop_and_controls_render_last():
    navigator = StackNavigator(Screen("root"))
    message_root = MessageRoot(navigator, access=Everyone(), timeout=None)
    view = commit_render(message_root)
    assert "## root" in sd.payload_texts(view)
    assert sd.payload_labels(view) == ["Back"]

    navigator.push(Screen("child"))
    interaction = interaction_harness()
    await message_root.refresh(interaction)
    pushed = interaction.response.edit_message.await_args.kwargs["view"]
    assert "## child" in sd.payload_texts(pushed)

    await message_root.dispatch("__nav_back", interaction_harness())
    assert navigator.current.name == "root"  # pyright: ignore[reportAttributeAccessIssue]  # pyrefly: ignore


async def test_home_appears_only_when_deep():
    navigator = StackNavigator(Screen("root"))
    message_root = MessageRoot(navigator, access=Everyone(), timeout=None)
    navigator.push(Screen("a"))
    navigator.push(Screen("b"))
    view = commit_render(message_root)
    assert "Home" in sd.payload_labels(view)

    await message_root.dispatch("__nav_home", interaction_harness())
    assert navigator.depth == 1


async def test_child_state_changes_rerender_through_the_shared_root():
    from squid_ui import state

    class Counting(Component[ComponentsV2Target]):
        count: int = state(0)

        @override
        def render(self):
            return [Text(f"count {self.count}")]

    child = Counting()
    navigator = StackNavigator(Screen("root"))
    message_root = MessageRoot(navigator, access=Everyone(), timeout=None)
    commit_render(message_root)
    navigator.push(child)
    commit_render(message_root)

    child.count = 5

    assert message_root.pending
    assert "count 5" in sd.payload_texts(commit_render(message_root))


async def test_close_finishes_the_root():
    navigator = StackNavigator(Screen("root"), show_close=True)
    message_root = MessageRoot(navigator, access=Everyone(), timeout=None)
    commit_render(message_root)

    await message_root.dispatch("__nav_close", interaction_harness())

    assert message_root.finished


async def test_same_screen_keeps_its_namespace_at_a_different_depth() -> None:
    child = Screen("retained")
    navigator = StackNavigator(child)
    runtime = ComponentRuntime(navigator, context={CHROME_CONTEXT: DEFAULT_CHROME})
    before = runtime.render()
    runtime.commit(before)
    retained_screen = select(child)
    namespace = query(index_tree(before), retained_screen).one().qualified_key

    navigator.push(Screen("middle"))
    runtime.commit(runtime.render(reuse_committed=True))
    navigator.push(child)
    after = runtime.render(reuse_committed=True)
    runtime.commit(after)

    assert query(index_tree(after), retained_screen).one().qualified_key == namespace
    navigator.home()
    home = runtime.render(reuse_committed=True)
    assert query(index_tree(home), retained_screen).one().qualified_key == namespace

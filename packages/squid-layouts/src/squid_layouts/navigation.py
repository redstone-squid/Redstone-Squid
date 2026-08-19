"""Stack navigation by composition.

`Navigator` renders whichever child component is on top of its stack and appends the
Back/Home/Close row *last, because it renders last* — no `__init_subclass__` machinery
rewriting subclass constructors to keep controls in order.
"""

import discord

from squid_layouts.component import Component
from squid_layouts.ir import Button, Node, Row, as_nodes


class Navigator(Component):
    """A component that shows one child at a time and owns the navigation controls."""

    def __init__(self, root: Component) -> None:
        self._stack: list[Component] = [root]

    @property
    def current(self) -> Component:
        return self._stack[-1]

    @property
    def depth(self) -> int:
        return len(self._stack)

    def push(self, child: Component) -> None:
        """Show ``child``, with Back leading to the current screen."""
        child._mount = self._mount
        self._stack.append(child)
        self.invalidate()

    def pop(self) -> None:
        if len(self._stack) > 1:
            self._stack.pop()
            self.invalidate()

    def home(self) -> None:
        if len(self._stack) > 1:
            del self._stack[1:]
            self.invalidate()

    def render(self) -> list[Node]:
        # Share the mount so a child mutating its own state re-renders the navigator's message.
        self.current._mount = self._mount
        nodes = as_nodes(self.current.render())
        chrome = self.mount.chrome
        controls = [
            Button(label=chrome.back, on_click=self._back, key="__nav_back", disabled=self.depth == 1),
        ]
        if self.depth > 2:
            controls.append(Button(label=chrome.home, on_click=self._home, key="__nav_home"))
        controls.append(
            Button(label=chrome.close, on_click=self._close, key="__nav_close", style=discord.ButtonStyle.secondary)
        )
        nodes.append(Row(tuple(controls)))
        return nodes

    async def _back(self, interaction: discord.Interaction) -> None:
        self.pop()

    async def _home(self, interaction: discord.Interaction) -> None:
        self.home()

    async def _close(self, interaction: discord.Interaction) -> None:
        await self.mount.finish_via(interaction)

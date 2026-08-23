"""Stack navigation by composition.

`Navigator` embeds whichever child component is on top of its stack and appends the
Back/Home/Close row *last, because it renders last* — no `__init_subclass__` machinery
rewriting subclass constructors to keep controls in order. It is an ordinary consumer of
`Component.embed`: the screens are children in the component tree, reaching the mount through
their parent rather than through a mount reference the navigator hands out.
"""

from squid_layouts.interactions import PressEvent
from squid_layouts.chrome import CHROME_CONTEXT
from squid_layouts.primitives.nodes import Button, Node, Row
from squid_layouts.primitives.styles import ActionStyle
from squid_layouts.runtime.component import Component


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
        # Keyed by depth: each screen owns its control namespace, so pushing the same child
        # class twice does not make the two copies share handlers.
        nodes: list = [self.boundary(self.current, key=f"s{self.depth - 1}")]
        chrome = self.inject(CHROME_CONTEXT)
        controls = [
            Button(label=chrome.back, on_click=self._back, key="__nav_back", disabled=self.depth == 1),
        ]
        if self.depth > 2:
            controls.append(Button(label=chrome.home, on_click=self._home, key="__nav_home"))
        controls.append(
            Button(label=chrome.close, on_click=self._close, key="__nav_close", style=ActionStyle.SECONDARY)
        )
        nodes.append(Row(tuple(controls)))
        return nodes

    async def _back(self, event: PressEvent) -> None:
        self.pop()

    async def _home(self, event: PressEvent) -> None:
        self.home()

    async def _close(self, event: PressEvent) -> None:
        await event.finish()

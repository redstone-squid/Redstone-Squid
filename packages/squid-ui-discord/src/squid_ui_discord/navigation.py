"""Stack navigation by composition.

`StackNavigator` embeds whichever child component is on top of its stack and appends the
Back/Home/Close row *last, because it renders last* — no `__init_subclass__` machinery
rewriting subclass constructors to keep controls in order. It is an ordinary consumer of
`Component.boundary`: the screens are children in the component tree, reaching the mount
through their parent rather than through a mount reference the navigator hands out.
"""

from squid_ui.chrome import CHROME_CONTEXT
from squid_ui.interactions import PressEvent
from squid_ui.primitives.nodes import Button, Row
from squid_ui.primitives.styles import ActionStyle
from squid_ui.runtime.component import Component
from squid_ui.semantic import LayoutNode
from squid_ui.target_types import ComponentsV2Target


class StackNavigator[RenderTargetT: ComponentsV2Target = ComponentsV2Target](Component[RenderTargetT]):
    """A component that shows one child at a time and owns the navigation controls.

    Back is disabled at the root, Home appears from depth 3, and Close finishes the mount.
    Labels come from the `CHROME_CONTEXT` the runtime publishes.
    """

    def __init__(self, root: Component[RenderTargetT]) -> None:
        self._stack: list[Component[RenderTargetT]] = [root]

    @property
    def current(self) -> Component[RenderTargetT]:
        """The child rendered now: the top of the stack."""
        return self._stack[-1]

    @property
    def depth(self) -> int:
        """Stack size; 1 at the root."""
        return len(self._stack)

    def push(self, child: Component[RenderTargetT]) -> None:
        """Show ``child``, with Back leading to the current screen."""
        self._stack.append(child)
        self.invalidate()

    def pop(self) -> None:
        """Return to the previous screen; a no-op at the root."""
        if len(self._stack) > 1:
            self._stack.pop()
            self.invalidate()

    def home(self) -> None:
        """Drop every screen above the root; a no-op at the root."""
        if len(self._stack) > 1:
            del self._stack[1:]
            self.invalidate()

    def render(self) -> list[LayoutNode[ComponentsV2Target]]:
        """The current screen under a depth-keyed boundary, then the control row."""
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

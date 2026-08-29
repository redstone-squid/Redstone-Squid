"""A keyed tab strip over alternate content regions."""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from squid_layouts.factories import controlled, destination, heading, navigation, stack
from squid_layouts.patterns._content import ContentItem, ContentLike, normalize_content, render_content, require_key
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.reactivity import state
from squid_layouts.semantic import LayoutNode, NavigateEvent, NavigationDisplay
from squid_layouts.text import TextLike


@dataclass(frozen=True, slots=True, init=False)
class Tab:
    """One tab and the content shown while it is selected."""

    key: str
    label: TextLike
    content: tuple[ContentItem, ...]

    def __init__(self, key: str, label: TextLike, content: ContentLike) -> None:
        require_key(key, name="Tab.key")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "content", normalize_content(content, name=f"Tab {key!r}.content"))


class Tabs(Component):
    """A component that selects one of several keyed content regions.

    The tab strip is semantic ``Navigation``: small sets become buttons and larger sets use
    the target's picker strategy. ``selected`` is component-owned presentation state, while
    ``key`` keeps the navigation identity stable across renders and embedding boundaries.
    """

    selected: str = state()

    def __init__(
        self,
        tabs: Iterable[Tab],
        *,
        key: str,
        initial: str | None = None,
        heading: TextLike | None = None,
        display: NavigationDisplay = NavigationDisplay.AUTO,
        on_change: Callable[[NavigateEvent], Awaitable[None]] | None = None,
    ) -> None:
        self.key = require_key(key, name="Tabs.key")
        self.tabs = tuple(tabs)
        if not self.tabs:
            message = "Tabs needs at least one tab"
            raise ValueError(message)
        keys = [tab.key for tab in self.tabs]
        if len(set(keys)) != len(keys):
            message = f"Tabs keys must be unique: {keys!r}"
            raise ValueError(message)
        if initial is not None and initial not in keys:
            message = f"Tabs.initial {initial!r} is not one of the tab keys"
            raise ValueError(message)
        self.heading = heading
        self.display = display
        self.on_change = on_change
        self.selected = initial or self.tabs[0].key

    @property
    def current(self) -> Tab:
        """The tab whose content is currently rendered."""
        return next(tab for tab in self.tabs if tab.key == self.selected)

    async def _select(self, event: NavigateEvent) -> None:
        if event.destination not in {tab.key for tab in self.tabs}:
            return
        self.selected = event.destination
        if self.on_change is not None:
            await self.on_change(event)

    def render(self) -> LayoutNode:
        current = self.current
        return stack(
            heading(self.heading) if self.heading is not None else None,
            navigation(
                *(destination(tab.label, key=tab.key) for tab in self.tabs),
                key=self.key,
                current=controlled(self.selected, self._select),
                display=self.display,
            ),
            *render_content(self, current.content, prefix=f"tab-{current.key}"),
        )

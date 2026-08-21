"""A semantic drill-down menu with breadcrumb navigation chrome."""

from collections.abc import Iterable
from dataclasses import dataclass

from squid_layouts.actions import ActionEvent
from squid_layouts.chrome import CHROME_CONTEXT, DEFAULT_CHROME
from squid_layouts.patterns._content import (
    ContentItem,
    ContentLike,
    normalize_content,
    render_content,
    require_key,
    slug,
)
from squid_layouts.runtime.component import Component
from squid_layouts.runtime.reactivity import state
from squid_layouts.semantic import (
    Action,
    ActionDisplay,
    Actions,
    Controlled,
    Destination,
    Heading,
    LayoutNode,
    NavigateEvent,
    Navigation,
    NavigationDisplay,
)
from squid_layouts.text import Message, ResolvedText, TextLike

_MISSING = object()


@dataclass(frozen=True, slots=True, init=False)
class MenuEntry:
    """One menu destination, optionally containing a nested submenu."""

    key: str
    label: TextLike
    content: tuple[ContentItem, ...]
    entries: tuple[MenuEntry, ...]

    def __init__(
        self,
        key_or_label: str,
        label_or_content: TextLike | ContentLike,
        content: ContentLike | object = _MISSING,
        *,
        key: str | None = None,
        entries: Iterable[MenuEntry] = (),
    ) -> None:
        if content is _MISSING:
            if key is None:
                resolved_key = slug(key_or_label)
            else:
                resolved_key = key
            label = key_or_label
            raw_content = label_or_content
        else:
            if key is not None:
                message = "MenuEntry's keyed form takes the key as its first argument"
                raise TypeError(message)
            resolved_key = key_or_label
            label = label_or_content
            raw_content = content
        require_key(resolved_key, name="MenuEntry.key")
        if not isinstance(label, str | ResolvedText | Message):
            message = "MenuEntry.label must be text"
            raise TypeError(message)
        child_entries = tuple(entries)
        if any(not isinstance(entry, MenuEntry) for entry in child_entries):
            message = "MenuEntry.entries must contain MenuEntry instances"
            raise TypeError(message)
        keys = [entry.key for entry in child_entries]
        if len(set(keys)) != len(keys):
            message = f"MenuEntry child keys must be unique: {keys!r}"
            raise ValueError(message)
        object.__setattr__(self, "key", resolved_key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "content", normalize_content(raw_content, name=f"MenuEntry {resolved_key!r}.content"))
        object.__setattr__(self, "entries", child_entries)


class Menu(Component):
    """A keyed menu that drills into destinations and owns Back/Home/Close actions."""

    path: tuple[str, ...] = state(())

    def __init__(
        self,
        title: TextLike,
        entries: Iterable[MenuEntry],
        *,
        key: str = "menu",
        initial: Iterable[str] = (),
        display: NavigationDisplay = NavigationDisplay.AUTO,
    ) -> None:
        self.key = require_key(key, name="Menu.key")
        self.title = title
        self.entries = tuple(entries)
        self.display = display
        self._validate_entries(self.entries, where="Menu.entries")
        initial_path = tuple(initial)
        self._resolve_path(initial_path)
        self.path = initial_path

    @staticmethod
    def _validate_entries(entries: tuple[MenuEntry, ...], *, where: str) -> None:
        keys = [entry.key for entry in entries]
        if len(set(keys)) != len(keys):
            message = f"{where} keys must be unique: {keys!r}"
            raise ValueError(message)
        for entry in entries:
            Menu._validate_entries(entry.entries, where=f"{where}.{entry.key}")

    def _resolve_path(self, path: tuple[str, ...]) -> tuple[MenuEntry | None, tuple[MenuEntry, ...]]:
        entries = self.entries
        current: MenuEntry | None = None
        for key in path:
            current = next((entry for entry in entries if entry.key == key), None)
            if current is None:
                message = f"Menu path contains unknown entry {key!r}"
                raise ValueError(message)
            entries = current.entries
        return current, entries

    @property
    def current(self) -> MenuEntry | None:
        """The selected destination, or ``None`` while the root menu is shown."""
        return self._resolve_path(self.path)[0]

    def back(self) -> None:
        """Return to the parent menu, if the menu is drilled down."""
        if self.path:
            self.path = self.path[:-1]

    def home(self) -> None:
        """Return to the root menu."""
        if self.path:
            self.path = ()

    async def _open(self, event: NavigateEvent) -> None:
        _current, entries = self._resolve_path(self.path)
        if any(entry.key == event.destination for entry in entries):
            self.path = (*self.path, event.destination)

    async def _back(self, event: ActionEvent) -> None:
        self.back()

    async def _home(self, event: ActionEvent) -> None:
        self.home()

    async def _close(self, event: ActionEvent) -> None:
        await event.finish()

    def _chrome(self) -> list[Action]:
        try:
            chrome = self.inject(CHROME_CONTEXT)
        except LookupError:
            chrome = DEFAULT_CHROME
        actions: list[Action] = []
        if self.path:
            actions.append(Action(f"{self.key}.back", chrome.back, self._back))
        if len(self.path) > 1:
            actions.append(Action(f"{self.key}.home", chrome.home, self._home))
        actions.append(Action(f"{self.key}.close", chrome.close, self._close))
        return actions

    def render(self) -> list[LayoutNode]:
        current, entries = self._resolve_path(self.path)
        nodes: list[LayoutNode] = [Heading(self.title)]
        if current is not None:
            nodes.append(Heading(current.label, level=3))
            nodes.extend(render_content(self, current.content, prefix="content"))
        if entries:
            nodes.append(
                Navigation(
                    self.key,
                    tuple(Destination(entry.key, entry.label) for entry in entries),
                    Controlled(None, self._open),
                    display=self.display,
                )
            )
        nodes.append(
            Actions(
                tuple(self._chrome()),
                key=f"{self.key}.chrome",
                display=ActionDisplay.INDIVIDUAL,
            )
        )
        return nodes

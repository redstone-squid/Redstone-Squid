"""A semantic drill-down menu with component and routed shells."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from squid_layouts.factories import actions, choice, heading, stack
from squid_layouts.runtime.component import RenderResult
from squid_layouts.semantic import ActionDisplay
from squid_layouts.text import Message, ResolvedText, TextLike
from squid_patterns._content import ContentItem, ContentLike, normalize_content, require_key, slug
from squid_patterns.shells import ComponentShell, PatternControls

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
            resolved_key = slug(key_or_label) if key is None else key
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
        object.__setattr__(self, "key", resolved_key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "content", normalize_content(raw_content, name=f"MenuEntry {resolved_key!r}.content"))
        object.__setattr__(self, "entries", child_entries)


@dataclass(frozen=True, slots=True)
class MenuState:
    """Serializable drill-down path for :class:`Menu`."""

    path: tuple[str, ...] = ()


class Menu:
    """A pure keyed drill-down menu."""

    def __init__(
        self,
        title: TextLike,
        entries: Iterable[MenuEntry],
        *,
        key: str = "menu",
        initial: Iterable[str] = (),
    ) -> None:
        self.key = require_key(key, name="Menu.key")
        self.title = title
        self.entries = tuple(entries)
        self._validate_entries(self.entries, where="Menu.entries")
        initial_path = tuple(initial)
        self._resolve_path(initial_path)
        self._initial_state = MenuState(initial_path)

    @property
    def initial_state(self) -> MenuState:
        return self._initial_state

    def build_component(self, *, initial: MenuState | None = None) -> ComponentShell[MenuState]:
        """Build the in-memory shell, with Close ending its mount."""
        return ComponentShell(self, initial=initial, finish_actions=("close",))

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

    def transition(
        self,
        state: MenuState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> MenuState:
        del submitted
        if action == "back":
            return MenuState(state.path[:-1])
        if action == "home":
            return MenuState()
        if action == "close":
            return state
        destination = values[0] if action == "open" and len(values) == 1 else action.removeprefix("open:")
        if action != "open" and not action.startswith("open:"):
            return state
        _current, entries = self._resolve_path(state.path)
        if destination not in {entry.key for entry in entries}:
            return state
        return MenuState((*state.path, destination))

    def render(self, state: MenuState, controls: PatternControls[MenuState]) -> RenderResult:
        current, entries = self._resolve_path(state.path)
        if len(entries) <= 5:
            destinations = (
                actions(
                    *(
                        controls.action(entry.label, f"open:{entry.key}", key=f"{self.key}.{entry.key}")
                        for entry in entries
                    ),
                    key=f"{self.key}.destinations",
                    display=ActionDisplay.INDIVIDUAL,
                )
                if entries
                else None
            )
        else:
            destinations = controls.choices(
                tuple(choice(entry.label, key=entry.key) for entry in entries),
                "open",
                key=f"{self.key}.destinations",
                selected=(),
                minimum=1,
                maximum=1,
                placeholder="Choose a destination",
            )
        chrome = actions(
            controls.action(controls.chrome.back, "back", key=f"{self.key}.back", available=bool(state.path)),
            controls.action(controls.chrome.home, "home", key=f"{self.key}.home", available=len(state.path) > 1),
            controls.action(controls.chrome.close, "close", key=f"{self.key}.close"),
            key=f"{self.key}.chrome",
            display=ActionDisplay.INDIVIDUAL,
        )
        return stack(
            heading(self.title),
            heading(current.label, level=3) if current is not None else None,
            *(controls.content(current.content, prefix="content") if current is not None else ()),
            destinations,
            chrome,
        )

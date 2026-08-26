"""A keyed tab pattern with component and routed shells."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from squid_layouts.factories import actions, choice, heading, stack
from squid_layouts.runtime.component import RenderResult
from squid_layouts.semantic import ActionDisplay
from squid_layouts.text import TextLike
from squid_patterns._content import ContentItem, ContentLike, normalize_content, require_key
from squid_patterns.shells import ComponentShell, PatternControls, PatternHandler


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


@dataclass(frozen=True, slots=True)
class TabsState:
    """Serializable selection state for :class:`Tabs`."""

    selected: str


class Tabs:
    """A pure keyed tab pattern with generic component and router shells."""

    def __init__(
        self,
        tabs: Iterable[Tab],
        *,
        key: str,
        initial: str | None = None,
        heading: TextLike | None = None,
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
        self._initial_state = TabsState(initial or self.tabs[0].key)

    @property
    def initial_state(self) -> TabsState:
        return self._initial_state

    def build_component(
        self,
        *,
        initial: TabsState | None = None,
        on_change: PatternHandler[TabsState] | None = None,
    ) -> ComponentShell[TabsState]:
        """Build the in-memory shell for this tab set."""
        return ComponentShell(self, initial=initial, on_change=on_change)

    def transition(
        self,
        state: TabsState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: Mapping[str, object] | None = None,
    ) -> TabsState:
        del submitted
        selected = values[0] if action == "select" and len(values) == 1 else action.removeprefix("select:")
        if action != "select" and not action.startswith("select:"):
            return state
        if selected not in {tab.key for tab in self.tabs}:
            return state
        return TabsState(selected)

    def render(self, state: TabsState, controls: PatternControls[TabsState]) -> RenderResult:
        current = next((tab for tab in self.tabs if tab.key == state.selected), self.tabs[0])
        if len(self.tabs) <= 5:
            selector = actions(
                *(
                    controls.action(
                        tab.label,
                        f"select:{tab.key}",
                        key=f"{self.key}.{tab.key}",
                        available=tab.key != current.key,
                    )
                    for tab in self.tabs
                ),
                key=self.key,
                display=ActionDisplay.INDIVIDUAL,
            )
        else:
            selector = controls.choices(
                tuple(choice(tab.label, key=tab.key) for tab in self.tabs),
                "select",
                key=self.key,
                selected=(current.key,),
                minimum=1,
                maximum=1,
                placeholder=current.label,
            )
        return stack(
            heading(self.heading) if self.heading is not None else None,
            selector,
            *controls.content(current.content, prefix=f"tab-{current.key}"),
        )

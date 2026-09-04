"""A keyed tab machine with component and routed shells."""

from collections.abc import Iterable
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, choice, heading, stack
from squid_ui.semantic import ControlDisplay
from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentItem, ContentLike, normalize_content, require_key
from squid_ui_widgets.drivers import ComponentDriver, FormValues, MachineControls, TransitionHandler


@dataclass(frozen=True, slots=True, init=False)
class Tab[RenderTargetT: RenderTarget = RenderTarget]:
    """One tab and the content shown while it is selected."""

    key: str
    label: TextLike
    content: tuple[ContentItem[RenderTargetT], ...]

    def __init__(self, key: str, label: TextLike, content: ContentLike[RenderTargetT]) -> None:
        require_key(key, name="Tab.key")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "content", normalize_content(content, name=f"Tab {key!r}.content"))


@dataclass(frozen=True, slots=True)
class TabsState:
    """Serializable selection state for :class:`Tabs`."""

    selected: str


class Tabs[RenderTargetT: RenderTarget = RenderTarget]:
    """A pure keyed tab machine with generic component and router shells."""

    def __init__(
        self,
        tabs: Iterable[Tab[RenderTargetT]],
        *,
        key: str,
        initial: str | None = None,
        title: TextLike | None = None,
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
        self.title = title
        self._initial_state = TabsState(initial or self.tabs[0].key)

    @property
    def initial_state(self) -> TabsState:
        """Return the configured initial selection."""
        return self._initial_state

    def build_component(
        self,
        *,
        initial: TabsState | None = None,
        on_change: TransitionHandler[TabsState] | None = None,
    ) -> ComponentDriver[TabsState, RenderTargetT]:
        """Build the in-memory shell for this tab set."""
        if initial is None:
            return ComponentDriver(self, on_change=on_change)
        return ComponentDriver(self, initial=initial, on_change=on_change)

    def transition(
        self,
        state: TabsState,
        action: str,
        *,
        values: tuple[str, ...] = (),
        submitted: FormValues | None = None,
    ) -> TabsState:
        """Select a known tab and ignore unrelated actions."""
        del submitted
        selected = values[0] if action == "select" and len(values) == 1 else action.removeprefix("select:")
        if action != "select" and not action.startswith("select:"):
            return state
        if selected not in {tab.key for tab in self.tabs}:
            return state
        return TabsState(selected)

    def render(
        self, state: TabsState, controls: MachineControls[TabsState, RenderTargetT]
    ) -> DocumentLike[RenderTargetT]:
        """Render the selected tab and an adaptive selector."""
        current = next((tab for tab in self.tabs if tab.key == state.selected), self.tabs[0])
        if len(self.tabs) <= 5:
            selector = action_controls(
                *(
                    controls.action_control(
                        tab.label,
                        f"select:{tab.key}",
                        key=f"{self.key}.{tab.key}",
                        available=tab.key != current.key,
                    )
                    for tab in self.tabs
                ),
                key=self.key,
                display=ControlDisplay.INDIVIDUAL,
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
            heading(self.title) if self.title is not None else None,
            selector,
            *controls.content(current.content, prefix=f"tab-{current.key}"),
        )

"""Switch among keyed tabs through a ComponentDriver or RouteDriver."""

from collections.abc import Iterable
from dataclasses import dataclass

from squid_ui.document import DocumentLike
from squid_ui.factories import action_controls, choice, heading, stack
from squid_ui.semantic import ControlDisplay, Emphasis, LayoutNode
from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets._content import ContentLike, normalize_content, require_key
from squid_ui_widgets.drivers import FormValues, MachineControls


@dataclass(frozen=True, slots=True, init=False)
class Tab[RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """One tab and the content shown while it is selected."""

    key: str
    label: TextLike
    content: tuple[LayoutNode[RenderTargetT, ContextT], ...]

    def __init__(self, key: str, label: TextLike, content: ContentLike[RenderTargetT, ContextT]) -> None:
        require_key(key, name="Tab.key")
        object.__setattr__(self, "key", key)
        object.__setattr__(self, "label", label)
        object.__setattr__(self, "content", normalize_content(content, name=f"Tab {key!r}.content"))


@dataclass(frozen=True, slots=True)
class TabsState:
    """Serializable selection state for :class:`Tabs`."""

    selected: str


class Tabs[RenderTargetT: RenderTarget = RenderTarget, ContextT = object]:
    """Describe tab content and deterministic selection transitions.

    TabsState stores the selected tab key; the first tab is the default. RouteDriver
    renders this definition when the host stores selection in routed control IDs.
    """

    def __init__(
        self,
        tabs: Iterable[Tab[RenderTargetT, ContextT]],
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
        self, state: TabsState, controls: MachineControls[TabsState, RenderTargetT, ContextT]
    ) -> DocumentLike[RenderTargetT, ContextT]:
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
                        emphasis=Emphasis.STRONG if tab.key == current.key else Emphasis.NORMAL,
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
            *controls.content(current.content),
        )

"""Mounted tabs with independent reactive selection state."""

from collections.abc import Iterable
from typing import override

from squid_ui.content.text import Text, TextLike
from squid_ui.target_types import RenderTarget
from squid_ui_widgets.drivers import ComponentDriver, TransitionHandler
from squid_ui_widgets.machines.tabs import Tab as Tab
from squid_ui_widgets.machines.tabs import Tabs as TabsMachine
from squid_ui_widgets.machines.tabs import TabsState as TabsState


class Tabs[RenderTargetT: RenderTarget = RenderTarget, ContextT = object](
    ComponentDriver[TabsState, RenderTargetT, ContextT]
):
    """Show a selector above the selected tab's content and notify on selection changes.

    On Discord, up to five tabs share one button row. Six or more use a select menu.
    The optional title goes above the selector.

    Each instance owns its selection. ``initial_state`` overrides ``initial``.
    ``machine`` exposes the configuration and pure transitions. ``machine_state``
    holds the current, nonpersistent selection.
    """

    @property
    @override
    def machine(self) -> TabsMachine[RenderTargetT, ContextT]:
        """The pure definition used by this component."""
        return self._definition

    def __init__(
        self,
        tabs: Iterable[Tab[RenderTargetT, ContextT]],
        *,
        key: str,
        initial: str | None = None,
        title: TextLike | None = None,
        initial_state: TabsState | None = None,
        on_change: TransitionHandler[TabsState] | None = None,
    ) -> None:
        title = None if title is None else Text(title)
        machine = TabsMachine(tabs, key=key, initial=initial, title=title)
        self._definition = machine
        super().__init__(
            machine, initial=machine.initial_state if initial_state is None else initial_state, on_change=on_change
        )

    def focus_targets(self) -> frozenset[str]:
        return frozenset(tab.key for tab in self.machine.tabs)

    def focus(self, target: str) -> None:
        """Select a tab without invoking the user-action callback."""
        if target not in self.focus_targets():
            message = f"Unavailable tab: {target!r}"
            raise ValueError(message)
        self.machine_state = TabsState(target)

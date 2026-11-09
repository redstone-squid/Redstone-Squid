"""Mounted tabs with independent reactive selection state."""

from collections.abc import Iterable
from typing import override

from squid_ui.target_types import RenderTarget
from squid_ui.text import TextLike
from squid_ui_widgets.drivers import ComponentDriver, TransitionHandler
from squid_ui_widgets.machines.tabs import Tab as Tab
from squid_ui_widgets.machines.tabs import Tabs as TabsMachine
from squid_ui_widgets.machines.tabs import TabsState as TabsState


class Tabs[RenderTargetT: RenderTarget = RenderTarget, ContextT = object](
    ComponentDriver[TabsState, RenderTargetT, ContextT]
):
    """Show one selected tab and notify on selection changes.

    Each instance owns its selection. ``initial_state`` overrides the starting tab
    chosen by ``initial``. Machine configuration and pure transitions are available
    through ``machine``; ``machine_state`` is the current, nonpersistent selection.
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
        machine = TabsMachine(tabs, key=key, initial=initial, title=title)
        self._definition = machine
        super().__init__(
            machine, initial=machine.initial_state if initial_state is None else initial_state, on_change=on_change
        )

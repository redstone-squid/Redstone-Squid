"""Frontend-neutral component runtime and presentation state."""

from squid_layouts.runtime.component import Component
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.presentation import (
    CursorState,
    DisclosureState,
    PresentationSession,
    SelectionState,
    StrategyState,
)
from squid_layouts.runtime.reactivity import ReactiveWriteError, batch, computed, state, transaction


def __getattr__(name: str):
    if name == "ComponentRuntime":
        from squid_layouts.runtime.owner import ComponentRuntime

        return ComponentRuntime
    raise AttributeError(name)


__all__ = [
    "Component",
    "ComponentRuntime",
    "ContextKey",
    "CursorState",
    "DisclosureState",
    "PresentationSession",
    "ReactiveWriteError",
    "SelectionState",
    "StrategyState",
    "batch",
    "computed",
    "state",
    "transaction",
]

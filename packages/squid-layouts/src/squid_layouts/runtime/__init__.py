"""Frontend-neutral component runtime and presentation state."""

from squid_layouts.runtime.component import Component
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.presentation import (
    ActivePagers,
    CursorState,
    CursorUpdate,
    DisclosureState,
    PresentationSession,
    SelectionState,
    SessionUpdate,
    StrategyState,
    StrategyUpdate,
    apply_updates,
)
from squid_layouts.runtime.reactivity import ReactiveWriteError, batch, computed, state, transaction


def __getattr__(name: str):
    if name == "ComponentRuntime":
        from squid_layouts.runtime.owner import ComponentRuntime

        return ComponentRuntime
    raise AttributeError(name)


__all__ = [
    "ActivePagers",
    "Component",
    "ComponentRuntime",
    "ContextKey",
    "CursorState",
    "CursorUpdate",
    "DisclosureState",
    "PresentationSession",
    "ReactiveWriteError",
    "SelectionState",
    "SessionUpdate",
    "StrategyState",
    "StrategyUpdate",
    "apply_updates",
    "batch",
    "computed",
    "state",
    "transaction",
]

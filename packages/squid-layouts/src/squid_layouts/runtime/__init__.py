"""Frontend-neutral component runtime and presentation state."""

from squid_layouts.runtime.component import Component
from squid_layouts.runtime.context import ContextKey
from squid_layouts.runtime.history import History, HistoryEntry, HistoryError, history, history_actions
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
    ToggleState,
    ToggleUpdate,
    apply_updates,
)
from squid_layouts.runtime.reactivity import (
    ReactiveWriteError,
    StateChange,
    StateDelta,
    UndeclaredStateError,
    batch,
    block_writes,
    computed,
    export_state,
    on_action_commit,
    restore_state,
    state,
    strict_state,
    transaction,
)
from squid_layouts.runtime.resources import (
    Failed,
    Pending,
    Ready,
    Resource,
    ResourceDelivery,
    ResourceNotReadyError,
    ResourceState,
    resource,
)


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
    "Failed",
    "History",
    "HistoryEntry",
    "HistoryError",
    "Pending",
    "PresentationSession",
    "ReactiveWriteError",
    "Ready",
    "Resource",
    "ResourceDelivery",
    "ResourceNotReadyError",
    "ResourceState",
    "SelectionState",
    "SessionUpdate",
    "StateChange",
    "StateDelta",
    "StrategyState",
    "StrategyUpdate",
    "ToggleState",
    "ToggleUpdate",
    "UndeclaredStateError",
    "apply_updates",
    "batch",
    "block_writes",
    "computed",
    "export_state",
    "history",
    "history_actions",
    "on_action_commit",
    "resource",
    "restore_state",
    "state",
    "strict_state",
    "transaction",
]

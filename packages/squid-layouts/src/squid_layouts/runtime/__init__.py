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
    ActionParticipant,
    CellReport,
    ComputedReport,
    Observation,
    ReactiveWriteError,
    SharedStateConflictError,
    StateChange,
    StateDelta,
    UndeclaredStateError,
    addresses,
    batch,
    block_writes,
    computed,
    export_state,
    inspect_cells,
    inspect_computed,
    join_action,
    on_action_commit,
    restore_state,
    state,
    transaction,
    untracked,
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
from squid_layouts.runtime.shared import Shared, cell


def __getattr__(name: str):
    if name == "ComponentRuntime":
        from squid_layouts.runtime.owner import ComponentRuntime

        return ComponentRuntime
    raise AttributeError(name)


__all__ = [
    "ActionParticipant",
    "ActivePagers",
    "CellReport",
    "Component",
    "ComponentRuntime",
    "ComputedReport",
    "ContextKey",
    "CursorState",
    "CursorUpdate",
    "DisclosureState",
    "Failed",
    "History",
    "HistoryEntry",
    "HistoryError",
    "Observation",
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
    "Shared",
    "SharedStateConflictError",
    "StateChange",
    "StateDelta",
    "StrategyState",
    "StrategyUpdate",
    "ToggleState",
    "ToggleUpdate",
    "UndeclaredStateError",
    "addresses",
    "apply_updates",
    "batch",
    "block_writes",
    "cell",
    "computed",
    "export_state",
    "history",
    "history_actions",
    "inspect_cells",
    "inspect_computed",
    "join_action",
    "on_action_commit",
    "resource",
    "restore_state",
    "state",
    "transaction",
    "untracked",
]

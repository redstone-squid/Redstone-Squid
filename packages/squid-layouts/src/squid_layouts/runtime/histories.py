"""Component-owned history derived from immutable committed action lineage."""

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.factories import action, action_group
from squid_layouts.interactions import ActionEvent
from squid_layouts.runtime.reactivity import (
    ActionCommit,
    ActionContext,
    ActionKind,
    CellPatchSet,
    ConditionalCellPatch,
    ReactiveConflictError,
    apply_conditional_patches,
    has_action_hook,
    on_action_commit,
    transaction,
)
from squid_layouts.semantic import ActionGroup
from squid_layouts.text import TextLike


class HistoryError(RuntimeError):
    """A history operation could not be honoured or was already reserved by this action."""


class HistoryEntryState(Enum):
    """The inspectable lifecycle of a retained inverse plan."""

    READY = "ready"
    REVERSING = "reversing"
    UNDONE = "undone"
    REAPPLYING = "reapplying"
    CONFLICTED = "conflicted"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class HistoryResultStatus(Enum):
    """The terminal result of one requested history operation."""

    APPLIED = "applied"
    EMPTY = "empty"
    CONFLICT = "conflict"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class HistoryOwner(Protocol):
    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class UndoPlan:
    """An atomic set of version-conditional physical inverses."""

    cells: tuple[ConditionalCellPatch, ...]

    @classmethod
    def from_commit(cls, commit: ActionCommit) -> UndoPlan:
        patches: CellPatchSet = commit.patches
        return cls(patches.inverse())


@dataclass(slots=True)
class HistoryEntry:
    """One retained committed action and the fresh plan that can reverse it."""

    label: TextLike
    original_action_id: object
    undo_plan: UndoPlan
    state: HistoryEntryState = HistoryEntryState.READY
    recorded_at: float = field(default_factory=time.monotonic)
    last_result: HistoryResult | None = None
    undo_action_id: object | None = None
    redo_plan: UndoPlan | None = None


@dataclass(frozen=True, slots=True)
class HistoryResult:
    """A typed undo or redo result; conflict always means no state changed."""

    status: HistoryResultStatus
    entry: HistoryEntry | None = None
    action_id: object | None = None
    conflict: object | None = None
    error: Exception | None = None

    @property
    def applied(self) -> bool:
        return self.status is HistoryResultStatus.APPLIED


type UndoResult = HistoryResult
type RedoResult = HistoryResult


@dataclass(frozen=True, slots=True)
class HistoryEntrySnapshot:
    """Safe diagnostic facts for one history entry."""

    label: str
    recorded_at: float
    action_id: str
    state: str
    conflict: str | None


@dataclass(frozen=True, slots=True)
class HistorySnapshot:
    """A read-only view of one component-owned history stack."""

    name: str
    limit: int
    undo: tuple[HistoryEntrySnapshot, ...]
    redo: tuple[HistoryEntrySnapshot, ...]


class History:
    """One component's bounded commit-derived undo stack."""

    def __init__(self, owner: HistoryOwner, *, limit: int = 20) -> None:
        if limit < 1:
            message = "a history needs room for at least one entry"
            raise ValueError(message)
        self._owner = owner
        self.limit = limit
        self._undone: list[HistoryEntry] = []
        self._redoable: list[HistoryEntry] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undone)

    @property
    def can_redo(self) -> bool:
        return bool(self._redoable)

    @property
    def undo_label(self) -> TextLike | None:
        return self._undone[-1].label if self._undone else None

    @property
    def redo_label(self) -> TextLike | None:
        return self._redoable[-1].label if self._redoable else None

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._undone)

    @property
    def redoable(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._redoable)

    def snapshot(self, name: str) -> HistorySnapshot:
        return HistorySnapshot(
            name,
            self.limit,
            tuple(_entry_snapshot(entry) for entry in self._undone),
            tuple(_entry_snapshot(entry) for entry in self._redoable),
        )

    def record(self, label: TextLike) -> None:
        """Retain the whole successful action once, using its committed patch lineage."""

        def committed(commit: ActionCommit, aftermath: object) -> None:
            if commit.context.kind in {ActionKind.UNDO, ActionKind.REDO}:
                return
            self._push(HistoryEntry(label, commit.context.action_id, UndoPlan.from_commit(commit)))

        self._reserve(committed)

    async def undo(self, action_id: object | None = None) -> UndoResult:
        """Conditionally reverse one retained action as a fresh ``UNDO`` action."""
        entry = self._select(self._undone, action_id)
        if entry is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        entry.state = HistoryEntryState.REVERSING
        context = ActionContext.create(
            f"Undo {entry.label}", kind=ActionKind.UNDO, reverses_action_id=entry.original_action_id
        )
        try:
            with transaction(action_context=context):
                apply_conditional_patches(entry.undo_plan.cells)

                def committed(commit: ActionCommit, aftermath: object) -> None:
                    entry.undo_action_id = commit.context.action_id
                    entry.redo_plan = UndoPlan.from_commit(commit)
                    entry.state = HistoryEntryState.UNDONE
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.action_id)
                    entry.last_result = result
                    self._remove_identity(self._undone, entry)
                    self._redoable.append(entry)
                    self._owner.invalidate()

                on_action_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.action_id, error.detail, error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        assert entry.last_result is not None
        return entry.last_result

    async def redo(self) -> RedoResult:
        """Reapply the inverse of the actual committed undo using its fresh lineage."""
        entry = self._select(self._redoable, None)
        if entry is None or entry.redo_plan is None:
            return HistoryResult(HistoryResultStatus.EMPTY)
        entry.state = HistoryEntryState.REAPPLYING
        context = ActionContext.create(
            f"Redo {entry.label}", kind=ActionKind.REDO, reapplies_action_id=entry.original_action_id
        )
        try:
            with transaction(action_context=context):
                apply_conditional_patches(entry.redo_plan.cells)

                def committed(commit: ActionCommit, aftermath: object) -> None:
                    entry.undo_plan = UndoPlan.from_commit(commit)
                    entry.state = HistoryEntryState.READY
                    result = HistoryResult(HistoryResultStatus.APPLIED, entry, commit.context.action_id)
                    entry.last_result = result
                    self._remove_identity(self._redoable, entry)
                    self._undone.append(entry)
                    self._owner.invalidate()

                on_action_commit(committed, key=self)
        except ReactiveConflictError as error:
            entry.state = HistoryEntryState.CONFLICTED
            result = HistoryResult(HistoryResultStatus.CONFLICT, entry, context.action_id, error.detail, error)
            entry.last_result = result
            self._owner.invalidate()
            return result
        assert entry.last_result is not None
        return entry.last_result

    def drop_conflicted(self, action_id: object | None = None) -> HistoryEntry | None:
        """Forget a conflicted entry without touching application state."""
        for stack in (self._undone, self._redoable):
            entry = self._select(stack, action_id)
            if entry is not None and entry.state is HistoryEntryState.CONFLICTED:
                self._remove_identity(stack, entry)
                self._owner.invalidate()
                return entry
        return None

    def clear(self) -> None:
        if not (self._undone or self._redoable):
            return
        self._undone.clear()
        self._redoable.clear()
        self._owner.invalidate()

    def _reserve(self, callback) -> None:
        if has_action_hook(self):
            message = "this action already used this history; only one history operation is allowed"
            raise HistoryError(message)
        on_action_commit(callback, key=self)

    def _push(self, entry: HistoryEntry) -> None:
        self._undone.append(entry)
        del self._undone[: max(0, len(self._undone) - self.limit)]
        self._redoable.clear()
        self._owner.invalidate()

    @staticmethod
    def _select(stack: list[HistoryEntry], action_id: object | None) -> HistoryEntry | None:
        if action_id is None:
            return stack[-1] if stack else None
        return next((entry for entry in reversed(stack) if entry.original_action_id == action_id), None)

    @staticmethod
    def _remove_identity(stack: list[HistoryEntry], entry: HistoryEntry) -> None:
        for index, candidate in enumerate(stack):
            if candidate is entry:
                del stack[index]
                return


class _HistoryField:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._slot = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._slot = f"__history_{name}"

    def __get__(self, instance: HistoryOwner | None, owner: type | None = None) -> History:
        if instance is None:
            return self  # type: ignore[bad-return]
        stack = instance.__dict__.get(self._slot)  # type: ignore[missing-attribute]
        if stack is None:
            stack = History(instance, limit=self._limit)
            instance.__dict__[self._slot] = stack  # type: ignore[missing-attribute]
        return stack


def history(*, limit: int = 20) -> History:
    """Declare an undo stack whose owner lifetime ends the retained inverse plans."""
    return _HistoryField(limit)  # type: ignore[bad-return]


def history_actions(stack: History, *, key: str = "history", chrome: Chrome = DEFAULT_CHROME) -> ActionGroup:
    """Build ordinary controls for the history's current LIFO entries."""

    async def undo(event: ActionEvent) -> None:
        await stack.undo()

    async def redo(event: ActionEvent) -> None:
        await stack.redo()

    return action_group(
        action(chrome.undo, undo, key=f"{key}.undo", available=stack.can_undo),
        action(chrome.redo, redo, key=f"{key}.redo", available=stack.can_redo),
        key=key,
    )


def inspect_histories(owner: HistoryOwner) -> tuple[HistorySnapshot, ...]:
    """Inspect declared histories without applying an inverse."""
    snapshots: list[HistorySnapshot] = []
    for klass in reversed(type(owner).__mro__):
        for name, descriptor in vars(klass).items():
            if isinstance(descriptor, _HistoryField):
                snapshots.append(getattr(owner, name).snapshot(name))
    return tuple(snapshots)


def _entry_snapshot(entry: HistoryEntry) -> HistoryEntrySnapshot:
    conflict = (
        None if entry.last_result is None or entry.last_result.conflict is None else str(entry.last_result.conflict)
    )
    return HistoryEntrySnapshot(
        str(entry.label), entry.recorded_at, str(entry.original_action_id), entry.state.value, conflict
    )


__all__ = [
    "History",
    "HistoryEntry",
    "HistoryEntrySnapshot",
    "HistoryEntryState",
    "HistoryError",
    "HistoryOwner",
    "HistoryResult",
    "HistoryResultStatus",
    "HistorySnapshot",
    "RedoResult",
    "UndoPlan",
    "UndoResult",
    "history",
    "history_actions",
    "inspect_histories",
]

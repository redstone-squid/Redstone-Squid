"""Undo and redo for actions whose effects reach outside the component tree.

The framework restores what it owns -- the state delta the action's transaction already
captured -- and the author supplies the inverse of whatever the action did to the world.
Neither half pretends to do the other's: nothing here can verify that `unarchive` inverts
`archive`, so history sequences, labels, and refuses to guess.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from squid_layouts.actions import ActionEvent
from squid_layouts.chrome import DEFAULT_CHROME, Chrome
from squid_layouts.factories import action, action_group
from squid_layouts.runtime.reactivity import (
    StateDelta,
    block_writes,
    has_action_hook,
    on_action_commit,
    transaction,
)
from squid_layouts.semantic import ActionGroup
from squid_layouts.text import TextLike

type Inverse = Callable[[], Awaitable[None]]

_INVERSE_BLOCK = (
    "an undo or redo inverse may not write component state: the framework restores the "
    "recorded values right after it, which would silently clobber the write. Re-read what "
    "you need in the handler, after `await history.undo()` returns"
)


class HistoryError(RuntimeError):
    """A history operation could not be honoured or was already reserved by this action."""


class HistoryOwner(Protocol):
    def invalidate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One recorded action: what to call it, what it changed, how to reverse it."""

    label: TextLike
    delta: StateDelta
    undo: Inverse | None = None
    redo: Inverse | None = None
    recorded_at: float = field(default_factory=time.monotonic)

    @property
    def redoable(self) -> bool:
        """Whether replaying this entry is honest.

        An entry that reached outside the tree needs an explicit `redo=`; replaying only its
        state would show the action applied while the world stayed reverted. One that never
        did is the framework's alone in both directions.
        """
        return self.undo is None or self.redo is not None


class History:
    """One component's undo stack. Declare it with :func:`history`."""

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
        """What undoing would reverse, for a notice or a labelled control."""
        return self._undone[-1].label if self._undone else None

    @property
    def redo_label(self) -> TextLike | None:
        return self._redoable[-1].label if self._redoable else None

    @property
    def entries(self) -> tuple[HistoryEntry, ...]:
        """The undo stack, oldest first."""
        return tuple(self._undone)

    @property
    def redoable(self) -> tuple[HistoryEntry, ...]:
        """The redo stack, oldest first."""
        return tuple(self._redoable)

    def record(self, label: TextLike, *, undo: Inverse | None = None, redo: Inverse | None = None) -> None:
        """Enter the action in flight into history, if it commits.

        Call this from a handler, after the write it describes. The state half is the
        transaction's own capture, so the entry covers *the whole action* -- including writes
        made after this call, and writes to components other than this one's owner. The world
        half is `undo`/`redo`, and it is the author's promise: pass `undo=` for an action that
        changed anything outside the tree, and `redo=` as well if replaying it is honest.
        """
        if undo is None and redo is not None:
            message = "redo= replays an action that nothing undoes; pass undo= as well"
            raise TypeError(message)

        def commit(delta: StateDelta) -> None:
            self._push(HistoryEntry(label, delta, undo, redo))

        self._on_action_commit(commit)

    async def undo(self) -> HistoryEntry | None:
        """Stage reversal of the most recent entry, world first.

        Returns the entry this action will pop if it commits, or `None` when the stack is
        empty. The external inverse cannot be rolled back: after one succeeds, do no
        unrelated fallible work in the same action.
        """
        if not self._undone:
            return None
        entry = self._undone[-1]
        with transaction():
            await self._reverse(entry.undo)
            entry.delta.restore_before()

            def commit(_: StateDelta) -> None:
                if not self._undone or self._undone[-1] is not entry:
                    return
                self._undone.pop()
                if entry.redoable:
                    self._redoable.append(entry)
                self._owner.invalidate()

            self._on_action_commit(commit)
        return entry

    async def redo(self) -> HistoryEntry | None:
        """Stage replay of the most recently undone entry, world first.

        Returns the entry this action will restore if it commits, or `None` when the stack
        is empty. As with undo, an external inverse cannot be rolled back, so do no
        unrelated fallible work after one succeeds in the same action.
        """
        if not self._redoable:
            return None
        entry = self._redoable[-1]
        with transaction():
            await self._reverse(entry.redo)
            entry.delta.restore_after()

            def commit(_: StateDelta) -> None:
                if not self._redoable or self._redoable[-1] is not entry:
                    return
                self._redoable.pop()
                self._undone.append(entry)
                self._owner.invalidate()

            self._on_action_commit(commit)
        return entry

    def clear(self) -> None:
        """Forget both stacks."""
        if not (self._undone or self._redoable):
            return
        self._undone.clear()
        self._redoable.clear()
        self._owner.invalidate()

    async def _reverse(self, inverse: Inverse | None) -> None:
        """Run the author's half first: a failed one must leave the reader's view alone.

        Nothing after this point is reached, so the entry stays on the stack and the error
        travels the normal handler path. The transaction rolls back whatever the undo handler
        had already written.
        """
        if inverse is None:
            return
        with block_writes(_INVERSE_BLOCK):
            await inverse()

    def _on_action_commit(self, callback: Callable[[StateDelta], None]) -> None:
        """Reserve this history for one operation and publish it with the action."""
        if has_action_hook(self):
            message = "this action already used this history; only one record, undo, or redo operation is allowed"
            raise HistoryError(message)
        on_action_commit(callback, key=self)

    def _push(self, entry: HistoryEntry) -> None:
        self._undone.append(entry)
        del self._undone[: max(0, len(self._undone) - self.limit)]
        self._redoable.clear()
        self._owner.invalidate()


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
            # Straight into __dict__: this is the descriptor's own storage, not a state write.
            instance.__dict__[self._slot] = stack  # type: ignore[missing-attribute]
        return stack


def history(*, limit: int = 20) -> History:
    """Declare an undo stack owned by this component.

    A descriptor, like `sl.state()`, and for the same reason: the stack must invalidate its
    owner when it changes, so undo and redo controls enable and disable themselves.
    """
    return _HistoryField(limit)  # type: ignore[bad-return]


def history_actions(stack: History, *, key: str = "history", chrome: Chrome = DEFAULT_CHROME) -> ActionGroup:
    """Undo and redo controls, each available only when the stack has something to do.

    Two ordinary EXCLUSIVE actions, so the mount's author lock and generation checks govern
    who may undo. Anything richer -- a notice, a label naming the entry, an authorization
    check of its own -- is `sl.action` plus `History.undo`, which is what this is.
    """

    async def undo(event: ActionEvent) -> None:
        await stack.undo()

    async def redo(event: ActionEvent) -> None:
        await stack.redo()

    return action_group(
        action(chrome.undo, undo, key=f"{key}.undo", available=stack.can_undo),
        action(chrome.redo, redo, key=f"{key}.redo", available=stack.can_redo),
        key=key,
    )


__all__ = [
    "History",
    "HistoryEntry",
    "HistoryError",
    "HistoryOwner",
    "Inverse",
    "history",
    "history_actions",
]

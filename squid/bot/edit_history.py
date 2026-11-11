"""Session-local receipts for conditional domain edits."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.ui import tr
from squid.core.errors import AuthorizationError, InvalidStateError
from squid_reactivity.actions import ConflictDetail
from squid_reactivity.errors import ReactiveConflictError, ReactiveWriteError
from squid_reactivity.internals import CURRENT_TRANSACTION
from squid_ui.runtime import ReversibleOperation


@dataclass(frozen=True, slots=True)
class _ConditionalEdit[ValueT]:
    """Reverse one saved value through an atomic domain compare-and-replace.

    The callback must compare and write in one transaction. Refresh runs only after
    that transaction succeeds; failure then requires reconciliation, not a retry.
    """

    target: str
    expected: ValueT
    replacement: ValueT
    authorize: Callable[[], Awaitable[bool]]
    replace: Callable[[ValueT, ValueT], Awaitable[bool]]
    refresh: Callable[[], Awaitable[None]]

    async def reverse(self) -> ReversibleOperation:
        if not await self.authorize():
            raise ReactiveConflictError(ConflictDetail(self.target, 0, 1), "Permission to edit was revoked")
        if not await self.replace(self.expected, self.replacement):
            raise ReactiveConflictError(ConflictDetail(self.target, 0, 1), "The saved value changed elsewhere")
        await self.refresh()
        return _ConditionalEdit(
            self.target, self.replacement, self.expected, self.authorize, self.replace, self.refresh
        )


async def save_edit[ValueT](
    stack: sl.runtime.History,
    label: sl.TextLike,
    before: ValueT,
    after: ValueT,
    *,
    target: str,
    authorize: Callable[[], Awaitable[bool]],
    replace: Callable[[ValueT, ValueT], Awaitable[bool]],
    refresh: Callable[[], Awaitable[None]],
) -> bool:
    """Authorize, conditionally save, retain an inverse, and refresh one user edit.

    Equal values do nothing. A stale value refreshes the view and raises a public
    conflict instead of allowing the screen to announce a successful save.
    """
    if before == after:
        return False
    current = CURRENT_TRANSACTION.get()
    if current is None or current.readonly:
        message = "saved edits require a writable action transaction"
        raise ReactiveWriteError(message)
    if not await authorize():
        raise AuthorizationError
    if not await replace(before, after):
        await refresh()
        raise InvalidStateError(
            tr(t"edit-history-save-conflict = This value changed elsewhere. Review it and try again.")
        )
    record_edit(stack, label, before, after, target=target, authorize=authorize, replace=replace, refresh=refresh)
    await refresh()
    return True


def record_edit[ValueT](
    stack: sl.runtime.History,
    label: sl.TextLike,
    before: ValueT,
    after: ValueT,
    *,
    target: str,
    authorize: Callable[[], Awaitable[bool]],
    replace: Callable[[ValueT, ValueT], Awaitable[bool]],
    refresh: Callable[[], Awaitable[None]],
) -> None:
    """Retain a successful operation whose domain service already performed the write."""
    if before != after:
        stack.record_operation(label, _ConditionalEdit(target, after, before, authorize, replace, refresh))


async def apply_history(stack: sl.runtime.History, event: sl.PressEvent, *, redo: bool = False) -> None:
    """Apply a history control and report the same outcomes in every screen."""
    result = await stack.redo() if redo else await stack.undo()
    if result.applied:
        label = result.entry.label if result.entry is not None else ""
        await event.notice(tr(t"edit-history-applied = Applied: {label}"))
    elif result.status is sl.runtime.HistoryResultStatus.CONFLICT:
        await event.notice(tr(t"edit-history-conflict = This edit changed elsewhere or is no longer permitted."))
    elif result.status is sl.runtime.HistoryResultStatus.NEEDS_RECONCILIATION:
        await event.notice(
            tr(t"edit-history-uncertain = The saved result needs checking. Refresh before editing again.")
        )
    elif result.status is sl.runtime.HistoryResultStatus.FAILED:
        await event.notice(tr(t"edit-history-failed = The edit could not be reversed."))


def history_controls(stack: sl.runtime.History) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
    """Render session edit controls, with truthful outcomes for external writes."""
    if not stack.can_undo and not stack.can_redo:
        return ()

    async def undo(event: sl.PressEvent) -> None:
        await apply_history(stack, event)

    async def redo(event: sl.PressEvent) -> None:
        await apply_history(stack, event, redo=True)

    return (
        sl.action_controls(
            sl.action_control(tr(t"edit-history-undo = Undo"), undo, key="edit-undo", available=stack.can_undo),
            sl.action_control(tr(t"edit-history-redo = Redo"), redo, key="edit-redo", available=stack.can_redo),
            key="edit-history",
        ),
    )

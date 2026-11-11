"""History controls and reversible values for unsaved widget editors."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import squid_ui as sl
import squid_ui_discord as sd
import squid_ui_widgets as sp
from squid.bot.ui import tr
from squid_reactivity.actions import ConflictDetail
from squid_reactivity.errors import ReactiveConflictError
from squid_ui.runtime import ReversibleOperation


@dataclass(frozen=True, slots=True)
class _LocalEdit[ValueT]:
    """Restore unsaved widget values while respecting the editor's current state."""

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
        return _LocalEdit(self.target, self.replacement, self.expected, self.authorize, self.replace, self.refresh)


def _record_local_edit[ValueT](
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
    """Capture widget values after a local transition."""
    if before != after:
        stack.record_operation(label, _LocalEdit(target, after, before, authorize, replace, refresh))


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


def track_local_edits[StateT, ValueT](
    stack: sl.runtime.History,
    driver: sp.ComponentDriver[StateT, sl.ComponentsV2Target, sd.Request],
    label: sl.TextLike,
    *,
    select: Callable[[StateT], ValueT],
    restore: Callable[[StateT, ValueT], StateT],
    enabled: Callable[[], bool],
) -> None:
    """Retain widget values while leaving its navigation state outside history."""
    original = driver.on_change

    async def allowed() -> bool:
        return enabled()

    async def replace_values(expected: ValueT, replacement: ValueT) -> bool:
        if select(driver.machine_state) != expected:
            return False
        driver.machine_state = restore(driver.machine_state, replacement)
        return True

    async def refresh() -> None:
        driver.invalidate()

    async def changed(event: sp.TransitionEvent[StateT]) -> None:
        if original is not None:
            await original(event)
        if enabled():
            _record_local_edit(
                stack,
                label,
                select(event.previous),
                select(event.state),
                target="local-editor",
                authorize=allowed,
                replace=replace_values,
                refresh=refresh,
            )

    driver.on_change = changed

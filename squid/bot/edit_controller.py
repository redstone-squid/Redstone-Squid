"""Session history orchestration; application handlers remain independent of the UI."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.edit_history import history_controls
from squid.core.actors import Actor
from squid.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from squid.core.i18n import tr
from squid.edits.application import EditRunner
from squid.edits.domain import (
    EditAttempt,
    EditCommand,
    EditCommitUncertainError,
    EditReceipt,
    EditResult,
    EditTarget,
    EditValues,
    Revision,
    RevisionConflictError,
)
from squid_reactivity.actions import ConflictDetail
from squid_reactivity.errors import ReactiveConflictError
from squid_reactivity.internals import CURRENT_TRANSACTION, Signal
from squid_ui.runtime import History, ReversibleOperation
from squid_ui.text import TextLike

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingEdit:
    command: EditCommand
    attempt: EditAttempt
    label: TextLike
    original_entry_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _ReceiptOperation:
    controller: EditController
    receipt: EditReceipt
    label: TextLike
    original_entry_id: UUID

    async def reverse(self) -> ReversibleOperation:
        expected = tuple(
            Revision(version.target, self.controller._heads[version.target]) for version in self.receipt.revisions
        )
        command = self.receipt.inverse(expected, redo=self.receipt.command.purpose == "undo")
        try:
            result = await self.controller._run(command, self.label, original_entry_id=self.original_entry_id)
        except (AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError) as error:
            raise ReactiveConflictError(ConflictDetail(self.receipt.command.kind, 0, 1), str(error)) from error
        finally:
            self.controller._retention_changed.set()
        assert result.receipt is not None
        return _ReceiptOperation(self.controller, result.receipt, self.label, self.original_entry_id)


@dataclass(frozen=True, slots=True)
class BoundEdit:
    """A command binding pinned to the revision displayed when an editor opens."""

    controller: EditController
    kind: str
    expected: tuple[Revision, ...]

    def writer[PatchT](
        self, encode: Callable[[PatchT], EditValues], label: TextLike
    ) -> Callable[[PatchT], Awaitable[None]]:
        async def save_patch(patch: PatchT) -> None:
            await self.save(encode(patch), label)

        return save_patch

    async def save(self, values: EditValues, label: TextLike) -> EditResult:
        return await self.controller.execute(EditCommand(self.kind, self.expected, values), label)


class EditController:
    """Own committed receipts and revision continuity for one mounted workspace.

    Bind the current actor provider and refresh callback once, rather than making
    every screen mutation assemble inverse callbacks. Pending outcomes block all
    further writes to the targets involved, including undo and redo.
    """

    def __init__(
        self,
        runner: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        refresh: Callable[[], Awaitable[None]],
        *,
        history: History | None = None,
    ) -> None:
        self.history = History(limit=20) if history is None else history
        self._runner = runner
        self._actor = actor
        self._refresh = refresh
        self._heads: dict[EditTarget, int] = {}
        self._pending: dict[UUID, PendingEdit] = {}
        self._lock = asyncio.Lock()
        self._refresh_failed = False
        self._signal = Signal()
        self._resource_operations: set[UUID] = set()
        self._retention_changed = asyncio.Event()

    def bind(self, kind: str, target: EditTarget, revision: int) -> BoundEdit:
        """Capture a displayed revision; later refreshes never rebase this binding."""
        return BoundEdit(self, kind, (Revision(target, revision),))

    def bind_many(self, kind: str, expected: tuple[Revision, ...]) -> BoundEdit:
        return BoundEdit(self, kind, expected)

    @property
    def refresh_failed(self) -> bool:
        self._signal.track()
        return self._refresh_failed

    @property
    def pending(self) -> tuple[PendingEdit, ...]:
        self._signal.track()
        return tuple(self._pending.values())

    def retained_operations(self) -> frozenset[UUID]:
        """Operations whose attachment resources remain reachable from this workspace."""
        receipts = [
            entry.operation.receipt
            for entry in (*self.history.entries, *self.history.redoable)
            if isinstance(entry.operation, _ReceiptOperation)
        ]
        receipts.extend(pending.attempt.receipt for pending in self.pending if pending.attempt.receipt is not None)
        retained = frozenset(receipt.command.operation_id for receipt in receipts if receipt.resources)
        # A confirmed reversal moves its history entry after the controller returns.
        # Keep both leases through that interval, including a slow refresh.
        return retained | self.owned_resource_operations if self._lock.locked() else retained

    @property
    def owned_resource_operations(self) -> frozenset[UUID]:
        return frozenset(self._resource_operations)

    def retention_released(self, operations: frozenset[UUID]) -> None:
        self._resource_operations.difference_update(operations)

    async def wait_retention_change(self) -> None:
        await self._retention_changed.wait()
        self._retention_changed.clear()

    def require_ready(self, targets: frozenset[str]) -> None:
        """Fence other session actions while a write's result still needs reconciliation."""
        if any(targets.intersection(v.target.identity for v in item.command.expected) for item in self.pending):
            raise InvalidStateError(tr(t"edits-pending = Check the previous saved result before editing this item."))

    async def execute(self, command: EditCommand, label: TextLike) -> EditResult:
        try:
            return await self._run(command, label)
        finally:
            self._retention_changed.set()

    async def _run(self, command: EditCommand, label: TextLike, *, original_entry_id: UUID | None = None) -> EditResult:
        current = CURRENT_TRANSACTION.get()
        if current is not None and current.readonly:
            message = "An edit requires a writable UI action"
            raise sl.runtime.ReactiveWriteError(message)
        async with self._lock:
            targets = {v.target for v in command.expected}
            self.require_ready(frozenset(target.identity for target in targets))
            retained = frozenset(
                target for entry in (*self.history.entries, *self.history.redoable) for target in entry.targets
            )
            for version in command.expected:
                known = self._heads.get(version.target)
                if (
                    isinstance(version, Revision)
                    and known is not None
                    and known != version.value
                    and version.target.identity in retained
                ):
                    raise RevisionConflictError(Revision(version.target, known), version.value)
            pending = PendingEdit(command, EditAttempt(), label, original_entry_id)
            try:
                result = await self._runner.execute(command, await self._actor(), attempt=pending.attempt)
            except BaseException:
                if pending.attempt.receipt is not None:
                    if pending.attempt.receipt.resources:
                        self._resource_operations.add(command.operation_id)
                    self._pending[command.operation_id] = pending
                    self._signal.notify()
                raise
            try:
                self._accept(result, pending)
            except BaseException:
                self._pending[command.operation_id] = pending
                self._signal.notify()
                raise
            await self.refresh()
            return result

    def _accept(self, result: EditResult, pending: PendingEdit) -> None:
        for version in result.outcome.revisions:
            self._heads[version.target] = version.value
        receipt = result.receipt
        if receipt is not None and receipt.resources:
            self._resource_operations.add(receipt.command.operation_id)
        self._retention_changed.set()
        if receipt is not None and result.outcome.changed:
            if receipt.final:
                self.history.discard_targets(receipt.targets)
            elif pending.original_entry_id is None:
                self.history.accept_committed(
                    pending.label,
                    _ReceiptOperation(self, receipt, pending.label, receipt.command.operation_id),
                    operation_id=receipt.command.operation_id,
                    targets=receipt.targets,
                )
        self._pending.pop(pending.command.operation_id, None)
        self._signal.notify()

    async def reconcile(self, operation_id: UUID) -> EditResult:
        current = CURRENT_TRANSACTION.get()
        if current is not None and current.readonly:
            message = "Reconciliation requires a writable UI action"
            raise sl.runtime.ReactiveWriteError(message)
        async with self._lock:
            pending = self._pending[operation_id]
            actor = await self._actor()
            result = await self._runner.reconcile(pending.command, actor, pending.attempt)
            if result is None:
                try:
                    result = await self._runner.execute(pending.command, actor, attempt=pending.attempt)
                except EditCommitUncertainError:
                    raise
                except AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError:
                    if pending.original_entry_id is not None:
                        self.history.settle_reversal(pending.original_entry_id, inverse=None)
                    self._pending.pop(operation_id, None)
                    self._signal.notify()
                    raise
            if pending.original_entry_id is not None:
                assert result.receipt is not None
                self.history.settle_reversal(
                    pending.original_entry_id,
                    inverse=_ReceiptOperation(self, result.receipt, pending.label, pending.original_entry_id),
                )
            self._accept(result, pending)
        await self.refresh()
        return result

    async def refresh(self) -> None:
        try:
            await self._refresh()
        except Exception:
            self._refresh_failed = True
            logger.warning("Committed edit retained; editor refresh failed", exc_info=True)
        else:
            self._refresh_failed = False
        self._signal.notify()

    async def discard(self, targets: frozenset[str]) -> None:
        async with self._lock:
            if any(
                targets.intersection(v.target.identity for v in pending.command.expected) for pending in self.pending
            ):
                raise InvalidStateError(
                    tr(t"edits-pending-discard = Check the saved result before discarding its history.")
                )
            self.history.discard_targets(targets)
            self._retention_changed.set()
            self._heads = {target: version for target, version in self._heads.items() if target.identity not in targets}
        await self.refresh()

    def controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        """Offer recovery and explicit history discard alongside ordinary undo and redo."""
        nodes = []
        pending = self.pending
        if not pending:
            nodes.extend(history_controls(self.history))
        for item in pending:

            async def check(event: sl.PressEvent, operation_id: UUID = item.command.operation_id) -> None:
                await self.reconcile(operation_id)
                await event.notice(tr(t"edits-result-checked = The saved result has been checked."))

            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"edits-check-result = Check saved result"), check, key=f"check-{item.command.operation_id}"
                    ),
                    key=f"edit-result-{item.command.operation_id}",
                )
            )
        if self.refresh_failed:

            async def refresh(event: sl.PressEvent) -> None:
                await self.refresh()

            nodes.append(
                sl.action_controls(
                    sl.action_control(tr(t"edits-refresh = Refresh saved values"), refresh, key="refresh-saved"),
                    key="edit-refresh",
                )
            )
        if not pending and (self.history.entries or self.history.redoable):

            async def discard(event: sl.PressEvent) -> None:
                targets = frozenset(
                    target for entry in (*self.history.entries, *self.history.redoable) for target in entry.targets
                )
                await self.discard(targets)

            nodes.append(
                sl.action_controls(
                    sl.action_control(
                        tr(t"edits-discard-reload = Discard history and reload"), discard, key="discard-history"
                    ),
                    key="edit-reload",
                )
            )
        return tuple(nodes)

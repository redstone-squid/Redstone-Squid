"""Save edits from an open editor and let the user undo and redo them. See docs/edits.md."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

import squid_ui as sl
import squid_ui_discord as sd
from squid.core.actors import Actor
from squid.core.errors import DomainError
from squid.core.i18n import tr
from squid.edits.application import EditRunner
from squid.edits.domain import (
    EditCommand,
    EditCommitUncertainError,
    EditReceipt,
    EditResult,
    EditTarget,
    EditValues,
    Revision,
)
from squid_reactivity.internals import CURRENT_TRANSACTION, Signal
from squid_ui.text import TextLike

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, eq=False)
class SavedEdit:
    """A committed edit in session history, with the label shown in undo and redo notices."""

    receipt: EditReceipt
    label: TextLike


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
    """Save one open editor's edits and keep its undo and redo history.

    Session history is a stack of saved edits. Undo writes an entry's values from
    before the edit, and redo writes its new values again. Both expect the revision
    this controller last saw for each target, so they fail with a conflict instead
    of overwriting a change made elsewhere.

    History only covers an unbroken run of this controller's own changes. Entries
    for a target are dropped when:

    - a save expected a revision other than the last one this controller saw, so
      the record changed elsewhere and undoing older entries would overwrite that;
    - an undo or redo fails, since retrying it would fail the same way;
    - a save cannot be undone (`final`), or its commit result is unknown;
    - the owning screen calls `forget`, for example after submitting a draft.
    """

    def __init__(
        self,
        runner: EditRunner,
        actor: Callable[[], Awaitable[Actor]],
        refresh: Callable[[], Awaitable[None]],
        *,
        limit: int = 20,
    ) -> None:
        self._runner = runner
        self._actor = actor
        self._refresh = refresh
        self._limit = limit
        # Mutated in place: forget() can run while an undo or redo awaits the database.
        self._done: list[SavedEdit] = []
        self._undone: list[SavedEdit] = []
        self._heads: dict[EditTarget, int] = {}
        self._lock = asyncio.Lock()
        self._refresh_failed = False
        self._signal = Signal()

    def bind(self, kind: str, target: EditTarget, revision: int) -> BoundEdit:
        """Capture a displayed revision; later refreshes never rebase this binding."""
        return BoundEdit(self, kind, (Revision(target, revision),))

    def bind_many(self, kind: str, expected: tuple[Revision, ...]) -> BoundEdit:
        return BoundEdit(self, kind, expected)

    @property
    def undoable(self) -> tuple[SavedEdit, ...]:
        """Saved edits that undo can reverse, oldest first."""
        self._signal.track()
        return tuple(self._done)

    @property
    def redoable(self) -> tuple[SavedEdit, ...]:
        """Undone edits that redo can apply again, the next one last."""
        self._signal.track()
        return tuple(self._undone)

    @property
    def refresh_failed(self) -> bool:
        self._signal.track()
        return self._refresh_failed

    async def execute(self, command: EditCommand, label: TextLike) -> EditResult:
        """Save an edit and add it to history if it changed anything and can be undone."""
        _require_writable_action()
        async with self._lock:
            changed_elsewhere = [
                version.target.identity
                for version in command.expected
                if (known := self._heads.get(version.target)) is not None
                and not (isinstance(version, Revision) and version.value == known)
            ]
            result = await self._run(command)
            self.forget(changed_elsewhere)
            receipt = result.receipt
            if receipt is not None and result.outcome.changed:
                if receipt.final:
                    self.forget(receipt.targets)
                else:
                    self._done.append(SavedEdit(receipt, label))
                    del self._done[: -self._limit]
                    self._undone.clear()
            self._accept(result)
            await self.refresh()
            return result

    async def undo(self) -> SavedEdit | None:
        """Write the latest saved edit's previous values. Return the entry, or None if history is empty."""
        return await self._step(self._done, self._undone, EditReceipt.undo)

    async def redo(self) -> SavedEdit | None:
        """Write the latest undone edit's values again. Return the entry, or None if there is none."""
        return await self._step(self._undone, self._done, EditReceipt.redo)

    async def _step(
        self,
        source: list[SavedEdit],
        destination: list[SavedEdit],
        command: Callable[[EditReceipt, tuple[Revision, ...]], EditCommand],
    ) -> SavedEdit | None:
        _require_writable_action()
        async with self._lock:
            if not source:
                return None
            entry = source[-1]
            expected = tuple(
                Revision(version.target, self._heads[version.target]) for version in entry.receipt.revisions
            )
            try:
                result = await self._run(command(entry.receipt, expected))
            except DomainError:
                self.forget(entry.receipt.targets)
                await self.refresh()
                raise
            if entry in source:
                source.remove(entry)
                destination.append(entry)
            self._accept(result)
            await self.refresh()
            return entry

    async def _run(self, command: EditCommand) -> EditResult:
        try:
            return await self._runner.execute(command, await self._actor())
        except EditCommitUncertainError:
            self.forget(version.target.identity for version in command.expected)
            await self.refresh()
            raise

    def _accept(self, result: EditResult) -> None:
        for version in result.outcome.revisions:
            self._heads[version.target] = version.value
        self._signal.notify()

    def forget(self, targets: Iterable[str]) -> None:
        """Drop history entries for these target identities, such as `"draft:<uuid>"`."""
        identities = frozenset(targets)
        if not identities:
            return
        self._done[:] = [entry for entry in self._done if not entry.receipt.targets & identities]
        self._undone[:] = [entry for entry in self._undone if not entry.receipt.targets & identities]
        self._heads = {target: value for target, value in self._heads.items() if target.identity not in identities}
        self._signal.notify()

    async def refresh(self) -> None:
        try:
            await self._refresh()
        except Exception:
            self._refresh_failed = True
            logger.warning("Committed edit retained; editor refresh failed", exc_info=True)
        else:
            self._refresh_failed = False
        self._signal.notify()

    def controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        """Render Undo and Redo for editors, followed by `refresh_controls`."""
        nodes: list[sl.LayoutNode[sl.ComponentsV2Target, sd.Request]] = []
        can_undo, can_redo = bool(self.undoable), bool(self.redoable)
        if can_undo or can_redo:

            async def undo(event: sl.PressEvent) -> None:
                await event.notice(await self._notice(self.undo))

            async def redo(event: sl.PressEvent) -> None:
                await event.notice(await self._notice(self.redo))

            nodes.append(
                sl.action_controls(
                    can_undo and sl.action_control(tr(t"edit-history-undo = Undo"), undo, key="edit-undo"),
                    can_redo and sl.action_control(tr(t"edit-history-redo = Redo"), redo, key="edit-redo"),
                    key="edit-history",
                )
            )
        return (*nodes, *self.refresh_controls())

    def refresh_controls(self) -> tuple[sl.LayoutNode[sl.ComponentsV2Target, sd.Request], ...]:
        """Render a refresh button if reloading saved values after a save failed.

        Settings screens use this without Undo and Redo, because using a toggle or form again
        reverses it. They still need the button: until a reload succeeds, the screen shows the
        values from before the save.
        """
        if not self.refresh_failed:
            return ()

        async def refresh(event: sl.PressEvent) -> None:
            await self.refresh()

        return (
            sl.action_controls(
                sl.action_control(tr(t"edits-refresh = Refresh saved values"), refresh, key="refresh-saved"),
                key="edit-refresh",
            ),
        )

    @staticmethod
    async def _notice(step: Callable[[], Awaitable[SavedEdit | None]]) -> TextLike:
        try:
            entry = await step()
        except EditCommitUncertainError as error:
            return error.public_text()
        except DomainError:
            return tr(t"edit-history-conflict = This edit changed elsewhere or is no longer permitted.")
        label = "" if entry is None else entry.label
        return tr(t"edit-history-applied = Applied: {label}")


def _require_writable_action() -> None:
    current = CURRENT_TRANSACTION.get()
    if current is not None and current.readonly:
        message = "An edit requires a writable UI action"
        raise sl.runtime.ReactiveWriteError(message)

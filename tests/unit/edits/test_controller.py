"""The shared controller owns version continuity and recovery, not individual screens."""

from functools import partial

import pytest

from squid.bot.edit_controller import EditController
from squid.core.errors import InvalidStateError
from squid.edits.application import EditExecutor
from squid.edits.domain import EditActor, RevisionConflictError
from squid_ui.runtime import HistoryResultStatus
from tests.support.unit_of_work import CommittedState
from tests.unit.edits.test_executor import ACTOR, TARGET, Handler, Store, Work, command


async def actor() -> EditActor:
    return ACTOR


async def refresh() -> None:
    pass


async def test_consecutive_undo_redo_tracks_own_revisions_without_rebasing_external_edits() -> None:
    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": Handler()}), actor, refresh)
    await controller.execute(command("First"), "First")
    await controller.execute(command("Second", 2), "Second")
    assert (await controller.history.undo()).applied
    assert state.value.revision == 4
    assert (await controller.history.undo()).applied
    assert state.value.values.unpack()["name"] == "Original"
    assert state.value.revision == 5
    assert (await controller.history.redo()).applied
    assert (await controller.history.redo()).applied
    assert state.value.revision == 7
    state.value.revision += 1
    assert (await controller.history.undo()).status is HistoryResultStatus.CONFLICT
    with pytest.raises(RevisionConflictError):
        await controller.execute(command("New", 8), "New")
    await controller.discard(frozenset({TARGET.identity}))
    await controller.execute(command("New", 8), "New")
    assert len(controller.history.entries) == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_undo_blocks_edits_and_recovers_without_double_apply(committed: bool) -> None:
    fail = False

    class SometimesFailingWork(Work):
        async def commit(self) -> None:
            if not fail or committed:
                await super().commit()
            if fail:
                raise ConnectionError("commit acknowledgement lost")

    state = CommittedState(Store())
    controller = EditController(
        EditExecutor(partial(SometimesFailingWork, state), {"example": Handler()}), actor, refresh
    )
    await controller.execute(command(), "Edit")
    fail = True
    assert (await controller.history.undo()).status is HistoryResultStatus.NEEDS_RECONCILIATION
    assert len(controller.pending) == 1
    with pytest.raises(InvalidStateError):
        await controller.execute(command("Blocked", state.value.revision), "Blocked")
    fail = False
    await controller.reconcile(controller.pending[0].command.operation_id)
    assert state.value.values.unpack()["name"] == "Original"
    assert state.value.revision == 3
    assert not controller.pending
    assert not controller.history.entries
    assert len(controller.history.redoable) == 1
    assert (await controller.history.redo()).applied
    assert state.value.revision == 4


async def test_refresh_failure_does_not_turn_a_committed_edit_into_an_uncertain_one() -> None:
    async def broken_refresh() -> None:
        raise RuntimeError("rendering failed")

    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": Handler()}), actor, broken_refresh)
    result = await controller.execute(command(), "Edit")
    assert result.outcome.changed
    assert controller.refresh_failed
    assert not controller.pending
    assert len(controller.history.entries) == 1
    assert (await controller.history.undo()).applied
    assert state.value.values.unpack()["name"] == "Original"

"""Session history undoes and redoes this controller's own saves, and never another writer's."""

from functools import partial

import pytest

from squid.bot.edit_controller import EditController
from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommitUncertainError, EditValues, RevisionConflictError
from squid_ui.testing import keys
from tests.support.unit_of_work import CommittedState
from tests.unit.edits.test_executor import ACTOR, TARGET, Handler, Store, Work, command


async def actor() -> Actor:
    return ACTOR


async def refresh() -> None:
    pass


def controller_for(state: CommittedState[Store], work: type[Work] = Work) -> EditController:
    return EditController(EditExecutor(partial(work, state), {"example": Handler()}), actor, refresh)


async def test_undo_and_redo_follow_the_controllers_own_revisions() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    await controller.execute(command("First"), "First")
    await controller.execute(command("Second", 2), "Second")
    assert await controller.undo() is not None
    assert state.value.values.unpack()["name"] == "First"
    assert await controller.undo() is not None
    assert state.value.values.unpack()["name"] == "Original"
    assert state.value.revision == 5
    assert await controller.redo() is not None
    assert await controller.redo() is not None
    assert state.value.values.unpack()["name"] == "Second"
    assert state.value.revision == 7
    assert await controller.redo() is None


async def test_a_change_made_elsewhere_ends_history_for_that_record() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    await controller.execute(command("First"), "First")
    state.value.revision += 1
    with pytest.raises(RevisionConflictError):
        await controller.undo()
    assert state.value.values.unpack()["name"] == "First"
    assert not controller.undoable
    # The editor has reloaded the record, so a save against the new revision works.
    await controller.execute(command("Later", 3), "Later")
    assert len(controller.undoable) == 1


async def test_saving_after_a_change_elsewhere_drops_older_entries() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    await controller.execute(command("First"), "First")
    state.value.revision += 1
    await controller.execute(command("Second", 3), "Second")
    # Undoing "First" now would overwrite whatever changed at revision 3.
    assert [entry.label for entry in controller.undoable] == ["Second"]


async def test_a_new_save_clears_redo() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    await controller.execute(command("First"), "First")
    await controller.undo()
    assert len(controller.redoable) == 1
    await controller.execute(command("Branch", 3), "Branch")
    assert not controller.redoable


@pytest.mark.parametrize("committed", [False, True])
async def test_an_uncertain_undo_forgets_history_without_blocking_edits(committed: bool) -> None:
    fail = False

    class SometimesFailingWork(Work):
        async def commit(self) -> None:
            if not fail or committed:
                await super().commit()
            if fail:
                raise ConnectionError("commit acknowledgement lost")

    state = CommittedState(Store())
    controller = controller_for(state, SometimesFailingWork)
    await controller.execute(command(), "Edit")
    fail = True
    with pytest.raises(EditCommitUncertainError):
        await controller.undo()
    assert not controller.undoable
    assert not controller.redoable
    fail = False
    await controller.execute(command("Next", state.value.revision), "Next")
    assert len(controller.undoable) == 1


async def test_refresh_failure_does_not_lose_a_committed_edit() -> None:
    async def broken_refresh() -> None:
        raise RuntimeError("rendering failed")

    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": Handler()}), actor, broken_refresh)
    result = await controller.execute(command(), "Edit")
    assert result.outcome.changed
    assert controller.refresh_failed
    assert len(controller.undoable) == 1
    assert await controller.undo() is not None
    assert state.value.values.unpack()["name"] == "Original"


async def test_settings_screens_get_refresh_without_undo() -> None:
    async def broken_refresh() -> None:
        raise RuntimeError("rendering failed")

    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": Handler()}), actor, broken_refresh)
    assert controller.refresh_controls() == ()

    await controller.execute(command(), "Edit")

    assert keys(controller.refresh_controls()) == ["edit-refresh", "refresh-saved"]
    # Editors show only the history step that can run: nothing has been undone, so no Redo.
    assert {"edit-undo", "refresh-saved"} <= set(keys(controller.controls()))
    assert "edit-redo" not in keys(controller.controls())


async def test_a_stale_form_fails_without_dropping_history() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    form = controller.bind("example", TARGET, 1)
    writer = form.writer(lambda name: EditValues.of({"name": name}), "Old form")
    await controller.bind("example", TARGET, 1).save(EditValues.of({"name": "Newer"}), "New form")
    with pytest.raises(RevisionConflictError):
        await writer("Stale")
    assert state.value.values.unpack()["name"] == "Newer"
    assert len(controller.undoable) == 1


async def test_forget_drops_entries_for_a_record() -> None:
    state = CommittedState(Store())
    controller = controller_for(state)
    await controller.execute(command(), "Edit")
    controller.forget({TARGET.identity})
    assert not controller.undoable
    assert await controller.undo() is None

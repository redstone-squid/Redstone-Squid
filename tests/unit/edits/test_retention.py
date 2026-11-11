"""Only a mounted parent renews resources still reachable through session history."""

from dataclasses import replace
from functools import partial
from typing import override
from uuid import UUID

import anyio

from squid.bot.edit_controller import EditController
from squid.bot.edit_retention import EditHistoryRetention
from squid.core.actors import Actor
from squid.edits.application import EditExecutor
from squid.edits.domain import EditCommand
from squid.edits.fields import PreparedFields
from squid.edits.retention import EditRetentionService
from tests.support.unit_of_work import CommittedState
from tests.unit.edits.test_controller import actor, refresh
from tests.unit.edits.test_executor import TARGET, Handler, Store, Values, Work, command


class ResourceHandler(Handler):
    async def prepare(self, uow: Work, actor: Actor, command: EditCommand) -> PreparedFields[Store, Values]:
        return replace(await super().prepare(uow, actor, command), resources=("attachment:file",))


class RetentionRecorder(EditRetentionService):
    def __init__(self) -> None:
        self.calls: list[tuple[frozenset[UUID], frozenset[UUID]]] = []

    @override
    async def sync(self, retained: frozenset[UUID], released: frozenset[UUID]) -> None:
        self.calls.append((retained, released))


async def test_retention_follows_undo_redo_branch_discard_and_parent_close() -> None:
    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": ResourceHandler()}), actor, refresh)
    recorder = RetentionRecorder()
    retention = EditHistoryRetention(controller, recorder)
    saved = await controller.execute(command(), "Edit")
    first = frozenset({saved.outcome.operation_id})
    await retention.sync()
    assert recorder.calls[-1] == (first, frozenset())
    assert (await controller.history.undo()).applied
    undo = retention.retained()
    await retention.sync()
    assert recorder.calls[-1] == (undo, first)
    await controller.execute(command("Branch", state.value.revision), "Branch")
    branch = retention.retained()
    await retention.sync()
    assert recorder.calls[-1] == (branch, undo)
    await controller.discard(frozenset({TARGET.identity}))
    await retention.sync()
    assert recorder.calls[-1] == (frozenset(), branch)
    await controller.execute(command("Final", state.value.revision), "Final")
    final = retention.retained()
    async with anyio.create_task_group() as tasks:
        await tasks.start(retention.run)
        tasks.cancel_scope.cancel()
    assert recorder.calls[-1] == (frozenset(), final)


async def test_close_releases_an_entry_evicted_before_the_first_renewal() -> None:
    state = CommittedState(Store())
    controller = EditController(EditExecutor(partial(Work, state), {"example": ResourceHandler()}), actor, refresh)
    recorder = RetentionRecorder()
    retention = EditHistoryRetention(controller, recorder)
    result = await controller.execute(command(), "Edit")
    controller.history.clear()
    async with anyio.create_task_group() as tasks:
        await tasks.start(retention.run)
        tasks.cancel_scope.cancel()
    assert any(result.outcome.operation_id in released for _, released in recorder.calls)
    assert not retention.owned

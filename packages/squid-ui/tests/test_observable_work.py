"""Framework-owned progress and history participate in ordinary reactive reads."""

import asyncio
import gc
import weakref
from collections.abc import Callable
from typing import override

import anyio
import pytest

import squid_ui as sl
from squid_reactivity import on_action_commit, on_action_rollback, transaction
from squid_reactivity.operations import OperationDefinition, Pending, ProgressReporter, Succeeded
from squid_ui.primitives import Text
from squid_ui.runtime import History, PostCommitOperation, UIExecutionHost, history
from squid_ui.runtime.owner import ComponentRuntime


class Reader(sl.Component[sl.ComponentsV2Target]):
    def __init__(self, read: Callable[[], str]) -> None:
        self.read = read

    @override
    def render(self) -> Text:
        return Text(self.read())


def mount(read: Callable[[], str]) -> ComponentRuntime[sl.ComponentsV2Target]:
    runtime = ComponentRuntime(Reader(read))
    runtime.commit(runtime.render())
    return runtime


async def test_operation_progress_updates_every_reader_including_computed_values() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    reporters: list[ProgressReporter[str]] = []

    async def work(progress: ProgressReporter[str]) -> int:
        reporters.append(progress)
        started.set()
        await release.wait()
        return 42

    execution = OperationDefinition(work, name="work", initial="queued").start()

    class Model(sl.Reactive):
        @sl.computed
        def label(self) -> str:
            match execution.status:
                case Pending(progress):
                    return progress
                case Succeeded(value):
                    return str(value)
                case _:
                    return "failed"

    model = Model()
    runtimes = [mount(lambda: model.label), mount(lambda: model.label)]
    unrelated = mount(lambda: "unchanged")

    async def run() -> None:
        assert await execution == 42

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(run)
        await started.wait()
        reporters[0].report("uploading")
        for runtime in runtimes:
            assert runtime.dirty
            tree = runtime.render(reuse_committed=True)
            assert tree.nodes == (Text("uploading"),)
            runtime.commit(tree)
        assert not unrelated.dirty
        release.set()

    for runtime in runtimes:
        assert runtime.dirty
        assert runtime.render(reuse_committed=True).nodes == (Text("42"),)
        runtime.finish()
    unrelated.finish()


async def test_model_history_updates_readers_without_a_component_owner() -> None:
    class Model(sl.Reactive):
        count: int = sl.state(0)
        edits: History = history()

        @sl.computed
        def label(self) -> str:
            return f"{self.edits.can_undo}/{self.edits.can_redo}"

    model = Model()
    runtimes = [mount(lambda: model.label), mount(lambda: model.label)]
    with transaction():
        model.edits.record("Increment")
        model.count = 1
    for runtime in runtimes:
        assert runtime.dirty
        tree = runtime.render(reuse_committed=True)
        assert tree.nodes == (Text("True/False"),)
        runtime.commit(tree)

    assert (await model.edits.undo()).applied
    for runtime in runtimes:
        assert runtime.dirty
        tree = runtime.render(reuse_committed=True)
        assert tree.nodes == (Text("False/True"),)
        runtime.commit(tree)
    assert (await model.edits.redo()).applied
    for runtime in runtimes:
        assert runtime.dirty
        runtime.commit(runtime.render(reuse_committed=True))
    model.edits.clear()
    for runtime in runtimes:
        assert runtime.dirty
        assert runtime.render(reuse_committed=True).nodes == (Text("False/False"),)
        runtime.finish()


def test_failed_recording_does_not_notify_history_readers() -> None:
    stack = History()
    runtime = mount(lambda: str(stack.can_undo))
    with pytest.raises(ValueError), transaction():
        stack.record("Failed")
        raise ValueError("abort")
    assert not runtime.dirty
    runtime.finish()


def test_retained_history_does_not_keep_its_reader_alive() -> None:
    stack = History()
    runtime = mount(lambda: str(stack.can_undo))
    reference = weakref.ref(runtime)
    del runtime
    gc.collect()
    assert reference() is None
    stack.clear()


async def test_post_commit_status_is_observable_without_starting_work_from_render() -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def work() -> int:
        started.set()
        await release.wait()
        return 42

    handle = PostCommitOperation(work, name="publish")
    runtime = mount(lambda: str(handle.status))
    assert not started.is_set()
    async with anyio.create_task_group() as tasks:
        host = UIExecutionHost()
        await tasks.start(host.run)
        with transaction():
            on_action_commit(lambda commit: handle._commit(commit, host))
        await started.wait()
        assert runtime.dirty
        tree = runtime.render(reuse_committed=True)
        assert tree.async_bindings == ()
        runtime.commit(tree)
        release.set()
        assert await handle == 42
        assert runtime.dirty
        assert runtime.render(reuse_committed=True).nodes == (Text("Succeeded(value=42)"),)
        await host.close()
    runtime.finish()


def test_post_commit_rollback_notifies_status_readers() -> None:
    async def work() -> None:
        pytest.fail("discarded work must not run")

    handle = PostCommitOperation(work, name="discard")
    runtime = mount(lambda: str(handle.status))
    with pytest.raises(ValueError), transaction():
        on_action_rollback(handle._discard)
        raise ValueError("abort")
    assert runtime.dirty
    assert runtime.render(reuse_committed=True).nodes == (Text("Discarded()"),)
    runtime.finish()

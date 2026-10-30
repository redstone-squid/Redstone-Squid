"""Services start on commit and finish cleaning up when their owner removes them."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, ExitStack, asynccontextmanager, contextmanager
from typing import override

import anyio
import pytest
from anyio.abc import TaskGroup, TaskStatus

from squid_ui import Component, ComponentsV2Target, DocumentLike, state
from squid_ui.primitives import Text
from squid_ui.runtime import (
    ComponentRuntime,
    ContextKey,
    LocalTopicBus,
    ManagedService,
    ServiceOwner,
    SharedState,
    Topic,
    UIExecutionHost,
)


@asynccontextmanager
async def running_host() -> AsyncIterator[UIExecutionHost]:
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            host = UIExecutionHost()
            await tasks.start(host.run)
            try:
                yield host
            finally:
                await host.close()


@pytest.fixture
def owner() -> ServiceOwner:
    return ServiceOwner()


class Recorder(ManagedService):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.task_groups: list[TaskGroup] = []
        self.stopped = asyncio.Event()

    @override
    async def run(self, *, task_status: TaskStatus[None]) -> None:
        self.events.append("activate")
        with ExitStack() as cleanup:
            cleanup.callback(self.events.append, "unsubscribe")
            try:
                async with anyio.create_task_group() as tasks:
                    self.task_groups.append(tasks)
                    await tasks.start(self.worker)
                    await self.setup()
                    task_status.started()
                    await anyio.sleep_forever()
            finally:
                with anyio.CancelScope(shield=True):
                    await self.cleanup()

    async def setup(self) -> None:
        pass

    async def cleanup(self) -> None:
        await anyio.sleep(0)
        self.events.append("cleanup")
        self.stopped.set()

    async def worker(self, *, task_status: TaskStatus[None]) -> None:
        try:
            self.events.append("work")
            task_status.started()
            await anyio.sleep_forever()
        finally:
            self.events.append("cancel")


async def test_ownerless_registration_and_exclusive_ownership() -> None:
    async with running_host() as host:
        service = Recorder()
        owner = ServiceOwner()
        assert owner.own(service) is service
        assert owner.own(service) is service
        assert service.events == []
        with pytest.raises(RuntimeError, match="another owner"):
            ServiceOwner().own(service)
        owner.activate(host)
        await owner.wait_ready()
        with pytest.raises(RuntimeError, match="sealed"):
            owner.own(Recorder())
        owner.cancel()
        await owner.wait()
        await owner.wait()
        assert service.events == ["activate", "work", "cancel", "cleanup", "unsubscribe"]
        with pytest.raises(RuntimeError, match="not active"):
            service.task_groups[0].start_soon(anyio.sleep_forever)


async def test_remount_uses_fresh_run_and_waits_for_previous_cleanup() -> None:
    async with running_host() as host:
        entered = asyncio.Event()
        release = asyncio.Event()

        class Slow(Recorder):
            @override
            async def cleanup(self) -> None:
                entered.set()
                await release.wait()
                await super().cleanup()

        owner = ServiceOwner()
        service = owner.own(Slow())
        owner.activate(host)
        await owner.wait_ready()
        owner.cancel()
        owner.activate(host)
        await entered.wait()
        # An intermediate activation cancelled before startup must still wait for the
        # first one's cleanup; a third activation cannot overtake either of them.
        owner.cancel()
        owner.activate(host)
        await anyio.sleep(0)
        assert len(service.task_groups) == 1
        release.set()
        await owner.wait_ready()
        assert len(service.task_groups) == 2
        assert service.task_groups[0] is not service.task_groups[1]
        with pytest.raises(RuntimeError, match="not active"):
            service.task_groups[0].start_soon(anyio.sleep_forever)
        owner.cancel()
        await owner.wait()


async def test_partial_activation_failure_and_cleanup_failures_all_settle() -> None:
    async with running_host() as host:
        events: list[str] = []
        reported = asyncio.Event()

        async def report(error: Exception) -> None:
            assert isinstance(error, ExceptionGroup)
            reported.set()

        def fail_cleanup() -> None:
            events.append("broken cleanup")
            raise ValueError("cleanup failed")

        class Broken(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                with ExitStack() as cleanup:
                    cleanup.callback(events.append, "last cleanup")
                    cleanup.callback(fail_cleanup)
                    raise ValueError("setup failed")

        owner = ServiceOwner()
        owner.own(Broken())
        good = owner.own(Recorder())
        owner.activate(host, on_error=report)
        await reported.wait()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()
        assert events == ["broken cleanup", "last cleanup"]
        assert good.stopped.is_set() or not good.events
        await owner.wait()


async def test_task_failure_cancels_siblings_and_reports_after_cleanup() -> None:
    async with running_host() as host:
        trigger = asyncio.Event()
        reported = asyncio.Event()
        service = Recorder()

        async def broken() -> None:
            await trigger.wait()
            raise ValueError("worker failed")

        async def report(error: Exception) -> None:
            assert service.stopped.is_set()
            reported.set()

        owner = ServiceOwner()
        owner.own(service)
        owner.activate(host, on_error=report)
        await owner.wait_ready()
        service.task_groups[0].start_soon(broken)
        trigger.set()
        await reported.wait()
        assert service.events[-3:] == ["cancel", "cleanup", "unsubscribe"]


async def test_contexts_and_async_callbacks_exit_in_reverse_order() -> None:
    async with running_host() as host:
        events: list[str] = []

        @contextmanager
        def sync_resource() -> Iterator[str]:
            try:
                yield "sync"
            finally:
                events.append("sync")

        @asynccontextmanager
        async def async_resource() -> AsyncIterator[str]:
            try:
                yield "async"
            finally:
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(0)
                    events.append("async")

        class Resources(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                with sync_resource() as sync_value:
                    async with async_resource() as async_value:
                        assert (sync_value, async_value) == ("sync", "async")
                        task_status.started()
                        await anyio.sleep_forever()

        owner = ServiceOwner()
        owner.own(Resources())
        owner.activate(host)
        await owner.wait_ready()
        owner.cancel()
        await owner.wait()
        assert events == ["async", "sync"]


async def test_host_cancellation_waits_for_async_cleanup() -> None:
    owner = ServiceOwner()
    service = owner.own(Recorder())
    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            host = UIExecutionHost()
            await tasks.start(host.run)
            owner.activate(host)
            await owner.wait_ready()
            tasks.cancel_scope.cancel()
    assert service.events[-3:] == ["cancel", "cleanup", "unsubscribe"]


KEY = ContextKey[Recorder]("service")


class Consumer(Component[ComponentsV2Target]):
    def __init__(self) -> None:
        self.borrowed: Recorder | None = None

    @override
    def render(self) -> Text:
        self.borrowed = self.inject(KEY)
        return Text("consumer")


class Panel(Component[ComponentsV2Target]):
    visible: bool = state(default=True)

    def __init__(self, borrowed: Recorder | None = None) -> None:
        self.service = borrowed if borrowed is not None else self.own(Recorder())
        self.child = Consumer()

    @override
    def render(self) -> DocumentLike[ComponentsV2Target]:
        self.provide(KEY, self.service)
        return self.boundary(self.child, key="child") if self.visible else Text("empty")


class OwnedPanel(Component[ComponentsV2Target]):
    def __init__(self, service: Recorder) -> None:
        self.service = self.own(service)

    @override
    def render(self) -> Text:
        return Text("owned")


async def test_committed_component_owns_activity_and_consumer_only_borrows() -> None:
    async with running_host() as host:
        panel = Panel()
        runtime = ComponentRuntime(panel, execution_host=host)
        candidate = runtime.render()
        assert not panel.service.events
        assert panel.child.borrowed is panel.service
        runtime.commit(candidate)
        await runtime.wait_services_ready()
        runtime.commit(runtime.render())
        panel.visible = False
        runtime.commit(runtime.render())
        await runtime.wait_services()
        assert len(panel.service.task_groups) == 1
        assert not panel.service.stopped.is_set()
        runtime.finish()
        await runtime.wait_services()
        assert panel.service.stopped.is_set()
        runtime.finish()
        await runtime.wait_services()


async def test_abandoned_candidate_and_borrowed_application_service() -> None:
    async with running_host() as host:
        abandoned = Panel()
        runtime = ComponentRuntime(abandoned, execution_host=host)
        runtime.render()
        runtime.finish()
        await runtime.wait_services()
        assert not abandoned.service.events

        application = ServiceOwner()
        service = application.own(Recorder())
        application.activate(host)
        await application.wait_ready()
        panel = Panel(service)
        runtime = ComponentRuntime(panel)
        runtime.commit(runtime.render())
        runtime.finish()
        assert not service.stopped.is_set()
        application.cancel()
        await application.wait()


async def test_nearest_provider_changes_without_transferring_service_ownership() -> None:
    async with running_host() as host:
        outer, inner = Recorder(), Recorder()
        consumer = Consumer()

        class Nested(Panel):
            overridden: bool = state(default=True)

            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                if self.overridden:
                    self.provide(KEY, self.service)
                return self.boundary(consumer, key="consumer")

        nested = Nested(inner)

        class Root(Panel):
            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                self.provide(KEY, self.service)
                return self.boundary(nested, key="nested")

        application = ServiceOwner()
        application.own(outer)
        application.own(inner)
        application.activate(host)
        await application.wait_ready()
        runtime = ComponentRuntime(Root(outer))
        runtime.commit(runtime.render())
        assert consumer.borrowed is inner
        nested.overridden = False
        runtime.commit(runtime.render())
        assert consumer.borrowed is outer
        runtime.finish()
        assert not outer.stopped.is_set()
        assert not inner.stopped.is_set()
        application.cancel()
        await application.wait()


async def test_injected_service_renders_before_readiness_and_publishes_ready_state() -> None:
    async with running_host() as host:
        bus = LocalTopicBus()
        release = asyncio.Event()

        class Status(SharedState):
            ready: bool = state(default=False)

        class Loading(Recorder):
            def __init__(self) -> None:
                super().__init__()
                self.status = Status(bus)

            @override
            async def setup(self) -> None:
                await release.wait()
                self.status.ready = True

        key = ContextKey[Loading]("loading-service")
        rendered: list[bool] = []

        class Reader(Component[ComponentsV2Target]):
            @override
            def render(self) -> Text:
                ready = self.inject(key).status.ready
                rendered.append(ready)
                return Text("ready" if ready else "loading")

        class Owner(Component[ComponentsV2Target]):
            def __init__(self) -> None:
                self.service = self.own(Loading())
                self.reader = Reader()

            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                self.provide(key, self.service)
                return self.boundary(self.reader, key="reader")

        panel = Owner()
        runtime = ComponentRuntime(panel, execution_host=host)
        tree = runtime.render()
        assert rendered == [False]
        assert not panel.service.events
        with ExitStack() as subscriptions:
            for address in tree.observations:
                subscriptions.callback(bus.subscribe(address, runtime.invalidate_address))
            runtime.commit(tree)
            release.set()
            await runtime.wait_services_ready()
            assert runtime.dirty
            runtime.commit(runtime.render())
            assert rendered == [False, True]
        runtime.finish()
        await runtime.wait_services()


async def test_independent_owners_and_unmount_hook_failure() -> None:
    async with running_host() as host:

        class BrokenPanel(Panel):
            @override
            def on_unmount(self) -> None:
                raise ValueError("unmount hook failed")

        first, second = BrokenPanel(), Panel()

        class Root(Component[ComponentsV2Target]):
            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                return self.boundary(first, key="first"), self.boundary(second, key="second")

        runtime = ComponentRuntime(Root(), execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        assert first.service is not second.service
        with pytest.raises(ExceptionGroup, match="lifecycle hooks"):
            runtime.finish()
        await runtime.wait_services()
        assert first.service.stopped.is_set()
        assert second.service.stopped.is_set()
        assert runtime.components == {}


def test_owned_services_require_host_and_render_cannot_register_services() -> None:
    with pytest.raises(RuntimeError, match="UIExecutionHost"):
        ComponentRuntime(Panel()).render()

    class Invalid(Component[ComponentsV2Target]):
        @override
        def render(self) -> Text:
            self.own(Recorder())
            return Text("invalid")

    with pytest.raises(RuntimeError, match="before rendering"):
        ComponentRuntime(Invalid()).render()


@pytest.mark.parametrize("cached", ["candidate", "committed", "backdated"])
async def test_cached_tree_requires_running_execution_host(cached: str) -> None:
    async with running_host() as host:

        class Values(SharedState):
            count: int = state(default=0)

        values = Values(LocalTopicBus())

        class ReadingPanel(Panel):
            @override
            def render(self) -> Text:
                return Text(str(values.count))

        panel = ReadingPanel()
        runtime = ComponentRuntime(panel, execution_host=host)
        tree = runtime.render()
        if cached != "candidate":
            runtime.commit(tree)
            await runtime.wait_services_ready()
        if cached == "committed":
            # Keep only the committed cache to exercise reuse between candidate renders.
            runtime._candidate_tree = None
        elif cached == "backdated":
            # A delayed notification for a value the committed tree already read.
            runtime.invalidate_addresses(tree.observations)
            assert runtime.dirty
        await host.close()
        with pytest.raises(RuntimeError, match="running UIExecutionHost"):
            runtime.render(reuse_committed=True)
        runtime.finish()
        await runtime.wait_services()


async def test_cancellation_during_async_acquisition_still_releases_resource() -> None:
    async with running_host() as host:
        entering, release, exited = asyncio.Event(), asyncio.Event(), asyncio.Event()

        @asynccontextmanager
        async def resource() -> AsyncIterator[None]:
            # Simulate an acquisition that completes despite owner cancellation.
            with anyio.CancelScope(shield=True):
                entering.set()
                await release.wait()
            try:
                yield
            finally:
                with anyio.CancelScope(shield=True):
                    await anyio.sleep(0)
                    exited.set()

        class Acquiring(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                async with resource():
                    task_status.started()
                    await anyio.sleep_forever()

        owner = ServiceOwner()
        owner.own(Acquiring())
        owner.activate(host)
        await entering.wait()
        owner.cancel()
        release.set()
        await owner.wait()
        assert exited.is_set()
        with pytest.raises(RuntimeError, match="not active"):
            await owner.wait_ready()


async def test_actual_component_remount_preserves_object_and_restarts_activity() -> None:
    async with running_host() as host:
        panel = Panel()

        class Root(Component[ComponentsV2Target]):
            visible: bool = state(default=True)

            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                return self.boundary(panel, key="panel") if self.visible else Text("empty")

        root = Root()
        runtime = ComponentRuntime(root, execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        service = panel.service
        root.visible = False
        runtime.commit(runtime.render())
        root.visible = True
        runtime.commit(runtime.render())
        await runtime.wait_services()
        await runtime.wait_services_ready()
        assert panel.service is service
        assert len(service.task_groups) == 2
        runtime.finish()
        await runtime.wait_services()


async def test_failed_mount_hook_cannot_leave_owned_work_running() -> None:
    async with running_host() as host:

        class Broken(Panel):
            @override
            def on_mount(self) -> None:
                raise ValueError("mount failed")

        panel = Broken()
        runtime = ComponentRuntime(panel, execution_host=host)
        with pytest.raises(ExceptionGroup, match="lifecycle hooks"):
            runtime.commit(runtime.render())
        await runtime.wait_services()
        assert not panel.service.events
        runtime.finish()


async def test_child_activity_cannot_wait_for_its_owner_or_host() -> None:
    async with running_host() as host:
        owner = ServiceOwner()
        service = owner.own(Recorder())
        owner.activate(host)
        await owner.wait_ready()
        checked = asyncio.Event()

        async def child() -> None:
            with pytest.raises(RuntimeError, match="own host"):
                await host.close()
            with anyio.CancelScope(shield=True):
                owner.cancel()
                with pytest.raises(RuntimeError, match="own owner's shutdown"):
                    await owner.wait()
                checked.set()

        service.task_groups[0].start_soon(child)
        await checked.wait()
        await owner.wait()


@pytest.mark.parametrize("shutdown", ["owner", "host"])
async def test_service_can_submit_shutdown_to_application_task_group(shutdown: str) -> None:
    async with running_host() as host, anyio.create_task_group() as application_tasks:
        proceed, finished = asyncio.Event(), asyncio.Event()
        owner = ServiceOwner()

        async def close() -> None:
            await proceed.wait()
            if shutdown == "host":
                await host.close()
            else:
                owner.cancel()
                await owner.wait()
            assert service.stopped.is_set()
            finished.set()

        class Submitting(Recorder):
            @override
            async def setup(self) -> None:
                application_tasks.start_soon(close)

        service = owner.own(Submitting())
        owner.activate(host)
        await owner.wait_ready()
        proceed.set()
        await finished.wait()


@pytest.mark.parametrize("allow_reentry", [False, True])
async def test_application_task_submitted_by_service_waits_for_component_cleanup(allow_reentry: bool) -> None:
    async with running_host() as host, anyio.create_task_group() as application_tasks:
        proceed, finished = asyncio.Event(), asyncio.Event()

        async def close() -> None:
            await proceed.wait()
            runtime.finish()
            await runtime.wait_services(allow_reentry=allow_reentry)
            assert panel.service.stopped.is_set()
            finished.set()

        class Submitting(Recorder):
            @override
            async def setup(self) -> None:
                application_tasks.start_soon(close)

        panel = OwnedPanel(Submitting())
        runtime = ComponentRuntime(panel, execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        proceed.set()
        await finished.wait()


async def test_failure_handler_cannot_close_its_host() -> None:
    async with running_host() as host:
        reported = asyncio.Event()

        class Broken(Recorder):
            @override
            async def setup(self) -> None:
                raise ValueError("setup failed")

        async def report(error: Exception) -> None:
            assert service.stopped.is_set()
            with pytest.raises(RuntimeError, match="own host"):
                await host.close()
            reported.set()

        owner = ServiceOwner()
        service = owner.own(Broken())
        owner.activate(host, on_error=report)
        await reported.wait()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()


async def test_readiness_observes_failure_without_waiting_for_earlier_setup() -> None:
    async with running_host() as host:
        pending = asyncio.Event()
        release = asyncio.Event()
        stopped = asyncio.Event()

        class Pending(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                try:
                    pending.set()
                    await release.wait()
                    task_status.started()
                    await anyio.sleep_forever()
                finally:
                    stopped.set()

        class Broken(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                await pending.wait()
                raise ValueError("later setup failed")

        owner = ServiceOwner()
        owner.own(Pending())
        owner.own(Broken())
        owner.activate(host)
        with pytest.raises(ExceptionGroup, match="unhandled errors") as failure:
            await owner.wait_ready()
        assert failure.value.subgroup(ValueError) is not None
        assert not stopped.is_set()
        release.set()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()
        assert stopped.is_set()


@pytest.mark.parametrize("end", ["cancel", "host", "setup", "worker"])
async def test_lexical_resources_outlive_workers_and_callbacks(end: str) -> None:
    async with running_host() as host:
        events: list[str] = []
        trigger = asyncio.Event()
        failed = asyncio.Event()

        class Resources(ManagedService):
            @asynccontextmanager
            async def client(self) -> AsyncIterator[None]:
                # Real clients can hide a task group and cancel scopes in their context.
                with anyio.CancelScope():
                    async with anyio.create_task_group() as tasks:
                        await tasks.start(self.client_worker)
                        try:
                            yield
                        finally:
                            with anyio.CancelScope(shield=True):
                                await anyio.sleep(0)
                                events.append("resource exit")
                            tasks.cancel_scope.cancel()

            async def client_worker(self, *, task_status: TaskStatus[None]) -> None:
                try:
                    task_status.started()
                    await anyio.sleep_forever()
                finally:
                    events.append("client stopped")

            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                async with self.client():
                    with ExitStack() as cleanup:
                        cleanup.callback(events.append, "callback")
                        async with anyio.create_task_group() as tasks:
                            await tasks.start(self.worker)
                            if end == "setup":
                                raise ValueError("setup failed")
                            task_status.started()
                            await anyio.sleep_forever()

            async def worker(self, *, task_status: TaskStatus[None]) -> None:
                try:
                    task_status.started()
                    await trigger.wait()
                    raise ValueError("worker failed")
                finally:
                    events.append("worker stopped")

        async def report(error: Exception) -> None:
            failed.set()

        owner = ServiceOwner()
        owner.own(Resources())
        owner.activate(host, on_error=report)
        if end == "setup":
            with pytest.raises(ExceptionGroup):
                await owner.wait_ready()
        else:
            await owner.wait_ready()
        if end == "host":
            await host.close()
        elif end == "worker":
            trigger.set()
            await failed.wait()
        owner.cancel()
        if end in {"setup", "worker"}:
            with pytest.raises(ExceptionGroup):
                await owner.wait()
        else:
            await owner.wait()
        assert events.index("worker stopped") < events.index("callback") < events.index("resource exit")
        assert "client stopped" in events


async def test_runtime_readiness_observes_all_committed_owners() -> None:
    async with running_host() as host:
        release = asyncio.Event()
        entered = asyncio.Event()

        class Pending(Recorder):
            @override
            async def setup(self) -> None:
                await super().setup()
                entered.set()
                await release.wait()

        class Broken(Recorder):
            @override
            async def setup(self) -> None:
                await entered.wait()
                raise ValueError("second owner failed")

        first, second = OwnedPanel(Pending()), OwnedPanel(Broken())

        class Root(Component[ComponentsV2Target]):
            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                return self.boundary(first, key="first"), self.boundary(second, key="second")

        runtime = ComponentRuntime(Root(), execution_host=host)
        await runtime.wait_services_ready()
        runtime.commit(runtime.render())
        with pytest.raises(ExceptionGroup):
            await runtime.wait_services_ready()
        assert not first.service.stopped.is_set()
        runtime.finish()
        with pytest.raises(ExceptionGroup):
            await runtime.wait_services()
        assert first.service.stopped.is_set()


async def test_cancelling_readiness_wait_does_not_cancel_services() -> None:
    async with running_host() as host:
        entered, release = asyncio.Event(), asyncio.Event()

        class Pending(Recorder):
            @override
            async def setup(self) -> None:
                await super().setup()
                entered.set()
                await release.wait()

        owner = ServiceOwner()
        service = owner.own(Pending())
        owner.activate(host)
        async with anyio.create_task_group() as observers:
            observers.start_soon(owner.wait_ready)
            await entered.wait()
            observers.cancel_scope.cancel()
        assert not service.stopped.is_set()
        release.set()
        await owner.wait_ready()
        owner.cancel()
        await owner.wait()


async def test_resource_entry_failure_unwinds_without_starting_activity() -> None:
    async with running_host() as host:
        events: list[str] = []

        class Broken(ManagedService):
            @asynccontextmanager
            async def client(self) -> AsyncIterator[None]:
                try:
                    raise ValueError("resource entry failed")
                    yield
                finally:
                    with anyio.CancelScope(shield=True):
                        await anyio.sleep(0)
                        events.append("resource released")

            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                async with self.client():
                    events.append("unexpected activation")
                    task_status.started()
                    await anyio.sleep_forever()

        owner = ServiceOwner()
        owner.own(Broken())
        owner.activate(host)
        with pytest.raises(ExceptionGroup):
            await owner.wait_ready()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()
        assert events == ["resource released"]


async def test_runtime_readiness_does_not_switch_to_a_remounted_activation() -> None:
    async with running_host() as host:
        panel = Panel()
        runtime = ComponentRuntime(panel, execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()

        async def remount() -> None:
            runtime.finish()
            runtime.commit(runtime.render())

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(remount)
            with pytest.raises(ExceptionGroup):
                await runtime.wait_services_ready()
        await runtime.wait_services_ready()
        assert len(panel.service.task_groups) == 2
        runtime.finish()
        await runtime.wait_services()


async def test_normal_return_completes_once_and_keeps_passive_state() -> None:
    async with running_host() as host:
        completed = asyncio.Event()

        class Finite(ManagedService):
            runs = 0

            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                self.runs += 1
                task_status.started()
                completed.set()

        owner = ServiceOwner()
        service = owner.own(Finite())
        owner.activate(host)
        await completed.wait()
        await owner.wait_ready()
        owner.activate(host)
        assert service.runs == 1
        owner.cancel()
        await owner.wait()
        assert service.runs == 1
        owner.activate(host)
        await owner.wait_ready()
        assert service.runs == 2
        owner.cancel()
        await owner.wait()


@pytest.mark.parametrize("signals", [0, 2])
async def test_readiness_requires_exactly_one_signal(signals: int) -> None:
    async with running_host() as host:
        cleaned = asyncio.Event()
        reported = asyncio.Event()

        class Invalid(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                try:
                    for _ in range(signals):
                        task_status.started()
                finally:
                    cleaned.set()

        async def report(error: Exception) -> None:
            assert cleaned.is_set()
            reported.set()

        owner = ServiceOwner()
        owner.own(Invalid())
        owner.activate(host, on_error=report)
        await reported.wait()
        with pytest.raises(ExceptionGroup):
            await owner.wait_ready()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()


async def test_async_exit_stack_attempts_remaining_cleanup_after_failure() -> None:
    async with running_host() as host:
        events: list[str] = []

        async def cleanup(label: str, *, fail: bool = False) -> None:
            await anyio.sleep(0)
            events.append(label)
            if fail:
                raise ValueError("cleanup failed")

        class Resources(ManagedService):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                stack = AsyncExitStack()
                async with stack:
                    stack.push_async_callback(cleanup, "last")
                    stack.push_async_callback(cleanup, "broken", fail=True)
                    try:
                        task_status.started()
                        await anyio.sleep_forever()
                    finally:
                        with anyio.CancelScope(shield=True):
                            await stack.aclose()

        owner = ServiceOwner()
        owner.own(Resources())
        owner.activate(host)
        await owner.wait_ready()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()
        assert events == ["broken", "last"]


async def test_owned_subscription_stops_updating_retained_shared_state() -> None:
    async with running_host() as host:
        bus = LocalTopicBus()
        topic = Topic("build", "42")

        class Updates(SharedState):
            count: int = state(default=0)

        class Subscription(ManagedService):
            def __init__(self) -> None:
                self.state = Updates(bus)

            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                def changed(_: Topic) -> None:
                    self.state.count += 1

                unsubscribe = bus.subscribe(topic, changed)
                try:
                    task_status.started()
                    await anyio.sleep_forever()
                finally:
                    unsubscribe()

        class Owned(Component[ComponentsV2Target]):
            def __init__(self) -> None:
                self.service = self.own(Subscription())

            @override
            def render(self) -> Text:
                return Text(str(self.service.state.count))

        panel = Owned()
        runtime = ComponentRuntime(panel, execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        retained = panel.service.state
        bus.publish(topic)
        assert retained.count == 1
        runtime.finish()
        await runtime.wait_services()
        bus.publish(topic)
        assert retained.count == 1
        retained.count = 7
        assert retained.count == 7


async def test_service_run_uses_host_context_not_activating_transaction() -> None:
    from contextvars import ContextVar

    from squid_ui.runtime import transaction

    context = ContextVar("service_test_context", default="unconfigured")
    bus = LocalTopicBus()

    class Values(SharedState):
        count: int = state(default=0)

    values = Values(bus)

    class Writer(ManagedService):
        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            values.count += 1
            assert context.get() == "host"
            task_status.started()
            await anyio.sleep_forever()

    with context.set("host"):
        async with running_host() as host:
            owner = ServiceOwner()
            owner.own(Writer())
            token = context.set("interaction")
            try:
                with transaction():
                    owner.activate(host)
                await owner.wait_ready()
            finally:
                context.reset(token)
                owner.cancel()
                await owner.wait()
            assert values.count == 1


async def test_failed_shutdown_does_not_retain_successfully_removed_services() -> None:
    import gc
    import weakref

    class Broken(Recorder):
        @override
        async def cleanup(self) -> None:
            await super().cleanup()
            raise ValueError("cleanup failed")

    class Root(Component[ComponentsV2Target]):
        def __init__(self, children: tuple[OwnedPanel, ...]) -> None:
            self.children = children

        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            return tuple(self.boundary(child, key=str(index)) for index, child in enumerate(self.children)) or Text(
                "empty"
            )

    async with running_host() as host:
        good = OwnedPanel(Recorder())
        reference = weakref.ref(good.service)
        root = Root((good, OwnedPanel(Broken())))
        runtime = ComponentRuntime(root, execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        root.children = ()
        root.invalidate()
        runtime.commit(runtime.render())
        with pytest.raises(ExceptionGroup):
            await runtime.wait_services()
        del good
        gc.collect()
        assert reference() is None
        # Completed observations release failed owners too.
        await runtime.wait_services()
        runtime.finish()


@pytest.mark.parametrize("child_task", [False, True])
@pytest.mark.parametrize("through_runtime", [False, True])
async def test_service_cannot_wait_for_its_own_readiness(child_task: bool, through_runtime: bool) -> None:
    async with running_host() as host:

        async def check() -> None:
            with pytest.raises(RuntimeError, match="own readiness"):
                await wait_ready()

        class Checking(Recorder):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                if child_task:
                    async with anyio.create_task_group() as tasks:
                        tasks.start_soon(check)
                else:
                    await check()
                task_status.started()
                # Once setup is complete, observing it from the run is safe.
                await wait_ready()
                await anyio.sleep_forever()

        if through_runtime:
            runtime = ComponentRuntime(OwnedPanel(Checking()), execution_host=host)
            runtime.commit(runtime.render())
            wait_ready = runtime.wait_services_ready
            cancel = runtime.finish
            wait = runtime.wait_services
        else:
            owner = ServiceOwner()
            owner.own(Checking())
            owner.activate(host)
            wait_ready = owner.wait_ready
            cancel = owner.cancel
            wait = owner.wait
        await wait_ready()
        cancel()
        await wait()


@pytest.mark.parametrize("close_first_host", [False, True])
async def test_changing_hosts_requires_cancelling_the_owner(close_first_host: bool, owner: ServiceOwner) -> None:
    async with running_host() as first, running_host() as second:
        service = owner.own(Recorder())
        owner.activate(first)
        await owner.wait_ready()
        if close_first_host:
            await first.close()
        with pytest.raises(RuntimeError, match=r"cancel.*another host"):
            owner.activate(second)
        assert service.events.count("activate") == 1
        owner.cancel()
        owner.activate(second)
        await owner.wait_ready()
        assert service.events == ["activate", "work", "cancel", "cleanup", "unsubscribe", "activate", "work"]
        owner.cancel()
        await owner.wait()


async def test_cancel_before_start_does_not_acquire_resources_or_report_failure(owner: ServiceOwner) -> None:
    async with running_host() as host:
        errors: list[Exception] = []

        async def report(error: Exception) -> None:
            errors.append(error)

        service = owner.own(Recorder())
        owner.activate(host, on_error=report)
        owner.cancel()
        owner.cancel()
        await owner.wait()
        await host.close()
        assert service.events == []
        assert errors == []
        with pytest.raises(RuntimeError, match="another owner"):
            ServiceOwner().own(service)


@pytest.mark.parametrize("during_setup", [False, True])
async def test_failed_run_restarts_only_after_cancel_without_inheriting_failure_history(
    during_setup: bool, owner: ServiceOwner
) -> None:
    async with running_host() as host:
        reported = asyncio.Event()
        calls = 0

        class FailsOnce(Recorder):
            @override
            async def run(self, *, task_status: TaskStatus[None]) -> None:
                nonlocal calls
                calls += 1
                if calls == 1:
                    if not during_setup:
                        task_status.started()
                    raise ValueError("first run failed")
                await super().run(task_status=task_status)

        async def report(error: Exception) -> None:
            reported.set()

        service = owner.own(FailsOnce())
        owner.activate(host, on_error=report)
        await reported.wait()
        owner.activate(host)
        with pytest.raises(ExceptionGroup) as failed_readiness:
            await owner.wait_ready()
        assert failed_readiness.value.subgroup(ValueError) is not None
        assert calls == 1
        owner.cancel()
        owner.activate(host, on_error=report)
        await owner.wait_ready()
        assert calls == 2
        # The replacement run does not inherit the completed run's failure.
        await owner.wait()
        assert not service.stopped.is_set()
        owner.cancel()
        for _ in range(2):
            await owner.wait()
        assert service.stopped.is_set()


async def test_cancelling_shutdown_wait_does_not_interrupt_cleanup(owner: ServiceOwner) -> None:
    async with running_host() as host:
        entered, release = asyncio.Event(), asyncio.Event()

        class Slow(Recorder):
            @override
            async def cleanup(self) -> None:
                entered.set()
                await release.wait()
                await super().cleanup()

        service = owner.own(Slow())
        owner.activate(host)
        await owner.wait_ready()
        owner.cancel()
        await entered.wait()
        async with anyio.create_task_group() as observers:
            observers.start_soon(owner.wait)
            await anyio.lowlevel.checkpoint()
            observers.cancel_scope.cancel()
        assert not service.stopped.is_set()
        release.set()
        await owner.wait()
        assert service.events[-3:] == ["cancel", "cleanup", "unsubscribe"]


@pytest.mark.parametrize("through_runtime", [False, True])
async def test_shutdown_observers_keep_run_failure_across_remount(through_runtime: bool) -> None:
    async with running_host() as host:
        cleaning, release = asyncio.Event(), asyncio.Event()
        reports: list[Exception] = []

        async def report(error: Exception) -> None:
            reports.append(error)

        class FailsOnce(Recorder):
            runs = 0

            @override
            async def cleanup(self) -> None:
                self.runs += 1
                if self.runs == 1:
                    cleaning.set()
                    await release.wait()
                    raise ValueError("first cleanup failed")
                await super().cleanup()

        panel = OwnedPanel(FailsOnce())
        owner = panel._owned_services
        assert owner is not None
        runtime = ComponentRuntime(panel, execution_host=host, on_service_error=report)

        def activate() -> None:
            if through_runtime:
                runtime.commit(runtime.render())
            else:
                owner.activate(host, on_error=report)

        def cancel() -> None:
            if through_runtime:
                runtime.finish()
            else:
                owner.cancel()

        async def wait() -> None:
            if through_runtime:
                await runtime.wait_services()
            else:
                await owner.wait()

        activate()
        await owner.wait_ready()
        cancel()
        await cleaning.wait()
        captured = [asyncio.Event(), asyncio.Event()]
        failures: list[ExceptionGroup] = []
        replacement_waiting = asyncio.Event()

        async def observe_replacement() -> None:
            replacement_waiting.set()
            await wait()

        async def observe(index: int) -> None:
            captured[index].set()
            with pytest.raises(ExceptionGroup) as failure:
                await wait()
            failures.append(failure.value)

        async with anyio.create_task_group() as observers:
            for index in range(2):
                observers.start_soon(observe, index)
            for event in captured:
                await event.wait()
            activate()
            observers.start_soon(observe_replacement)
            await replacement_waiting.wait()
            release.set()

        assert len(failures) == 2
        assert all(failure.subgroup(ValueError) is not None for failure in failures)
        await owner.wait_ready()
        cancel()
        await wait()
        await wait()
        assert panel.service.stopped.is_set()
        await host.close()
        assert len(reports) == 1


@pytest.mark.parametrize("fail_child", [False, True])
async def test_parent_service_outlives_child_cleanup(fail_child: bool) -> None:
    async with running_host() as host:
        entered, release = asyncio.Event(), asyncio.Event()
        parent = Recorder()

        class Child(Recorder):
            @override
            async def cleanup(self) -> None:
                entered.set()
                await release.wait()
                assert not parent.stopped.is_set()
                await super().cleanup()
                if fail_child:
                    raise ValueError("child cleanup failed")

        child = OwnedPanel(Child())

        class Parent(Component[ComponentsV2Target]):
            def __init__(self) -> None:
                self.service = self.own(parent)

            @override
            def render(self) -> DocumentLike[ComponentsV2Target]:
                return self.boundary(child, key="child")

        runtime = ComponentRuntime(Parent(), execution_host=host)
        runtime.commit(runtime.render())
        await runtime.wait_services_ready()
        runtime.finish()
        await entered.wait()
        try:
            assert not parent.stopped.is_set()
        finally:
            release.set()
        if fail_child:
            with pytest.raises(ExceptionGroup):
                await runtime.wait_services()
        else:
            await runtime.wait_services()
        assert parent.stopped.is_set()
        assert child.service.stopped.is_set()

"""Exercise parent setup, child borrowing, closure, and remount through the public API."""

import asyncio
from typing import override

import anyio
import pytest
from anyio.abc import TaskStatus

from squid_ui import Component, ComponentsV2Target, DocumentLike
from squid_ui.primitives import Text
from squid_ui.runtime import ComponentRuntime, ManagedService, ServiceHost, ServiceRun, ServiceRunStatus


@pytest.mark.parametrize("outcome", ["cancel_setup", "fail_setup", "complete", "ready"])
async def test_child_observes_one_parent_run_across_panel_remount(outcome: str) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    observed: list[ServiceRun] = []
    reports: list[Exception] = []

    class Connection(ManagedService):
        connected = False
        runs = 0

        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            self.runs += 1
            entered.set()
            await release.wait()
            if self.runs == 1 and outcome == "fail_setup":
                raise ValueError("connection failed")
            self.connected = True
            try:
                task_status.started()
                if self.runs == 1 and outcome == "complete":
                    return
                await anyio.sleep_forever()
            finally:
                self.connected = False

    connection = Connection()
    child_started = asyncio.Event()

    class Borrower(ManagedService):
        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            parent_run = connection.current_run
            assert parent_run is not None
            observed.append(parent_run)
            child_started.set()
            await parent_run.wait_ready()
            task_status.started()
            if parent_run.status is ServiceRunStatus.COMPLETED:
                return
            assert connection.connected
            try:
                await anyio.sleep_forever()
            finally:
                assert connection.connected

    class Child(Component[ComponentsV2Target]):
        def __init__(self) -> None:
            self.borrower = self.own(Borrower())

        @override
        def render(self) -> Text:
            return Text("Connection view")

    class Parent(Component[ComponentsV2Target]):
        def __init__(self) -> None:
            self.connection = self.own(connection)
            self.child = Child()

        @override
        def render(self) -> DocumentLike[ComponentsV2Target]:
            return self.boundary(self.child, key="child")

    async def report(error: Exception) -> None:
        reports.append(error)

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tasks:
            host = ServiceHost()
            await tasks.start(host.run)
            runtime = ComponentRuntime(Parent(), service_host=host, on_service_error=report)
            runtime.commit(runtime.render())
            await entered.wait()
            await child_started.wait()
            first = observed[0]
            assert first.status is ServiceRunStatus.STARTING
            if outcome != "cancel_setup":
                release.set()
                if outcome == "fail_setup":
                    with pytest.raises(ExceptionGroup):
                        await runtime.wait_services_ready()
                else:
                    await runtime.wait_services_ready()
            runtime.finish()
            if outcome == "fail_setup":
                with pytest.raises(ExceptionGroup):
                    await runtime.wait_services()
            else:
                await runtime.wait_services()
            terminal = first.status
            assert (
                terminal
                is {
                    "cancel_setup": ServiceRunStatus.CANCELLED,
                    "fail_setup": ServiceRunStatus.FAILED,
                    "complete": ServiceRunStatus.COMPLETED,
                    "ready": ServiceRunStatus.CANCELLED,
                }[outcome]
            )
            release.set()
            runtime.commit(runtime.render())
            await runtime.wait_services_ready()
            assert observed[-1] is connection.current_run
            assert observed[-1] is not first
            assert first.status is terminal
            if outcome == "fail_setup":
                with pytest.raises(ExceptionGroup) as failure:
                    await first.wait()
                assert failure.value is first.error
            else:
                await first.wait()
            runtime.finish()
            await runtime.wait_services()
            await host.close()

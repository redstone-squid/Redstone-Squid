"""Public service run observations survive remounts without retaining unobserved failures."""

import gc
import weakref
from typing import override

import anyio
import pytest
from anyio.abc import TaskStatus

from squid_ui.runtime import ManagedService, ServiceHost, ServiceOwner, ServiceRunStatus


@pytest.mark.parametrize("fail", [False, True])
async def test_run_outcome_survives_owner_shutdown_and_remount(fail: bool) -> None:
    class Finite(ManagedService):
        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            task_status.started()
            if fail:
                raise ValueError("run failed")

    async def report(error: Exception) -> None:
        pass

    async with anyio.create_task_group() as tasks:
        host = ServiceHost()
        await tasks.start(host.run)
        owner = ServiceOwner()
        service = owner.own(Finite())
        assert service.current_run is None
        owner.activate(host, on_error=report)
        first = service.current_run
        assert first is not None
        assert first.status is ServiceRunStatus.STARTING
        for _ in range(2):
            if fail:
                with pytest.raises(ExceptionGroup) as result:
                    await first.wait()
                assert result.value is first.error
                assert first.status is ServiceRunStatus.FAILED
            else:
                await first.wait()
                await first.wait_ready()
                assert first.status is ServiceRunStatus.COMPLETED
        owner.cancel()
        if fail:
            with pytest.raises(ExceptionGroup):
                await owner.wait()
        else:
            await owner.wait()
        assert service.current_run is None
        owner.activate(host, on_error=report)
        assert service.current_run is not first
        assert first.status is (ServiceRunStatus.FAILED if fail else ServiceRunStatus.COMPLETED)
        owner.cancel()
        await owner.wait()
        await host.close()


async def test_completed_owner_wait_releases_failed_run_locals() -> None:
    class Resource:
        pass

    references: list[weakref.ReferenceType[Resource]] = []

    class Broken(ManagedService):
        @override
        async def run(self, *, task_status: TaskStatus[None]) -> None:
            resource = Resource()
            references.append(weakref.ref(resource))
            raise ValueError("setup failed")

    async def report(error: Exception) -> None:
        pass

    async with anyio.create_task_group() as tasks:
        host = ServiceHost()
        await tasks.start(host.run)
        owner = ServiceOwner()
        service = owner.own(Broken())
        owner.activate(host, on_error=report)
        with pytest.raises(ExceptionGroup):
            await owner.wait_ready()
        owner.cancel()
        with pytest.raises(ExceptionGroup):
            await owner.wait()
        await host.close()
    gc.collect()
    assert service.current_run is None
    assert references[0]() is None

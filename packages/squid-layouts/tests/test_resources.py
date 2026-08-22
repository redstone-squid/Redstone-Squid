"""Reactive async resources before a frontend chooses how to deliver them."""

from collections.abc import Awaitable, Callable

import anyio
import pytest

from squid_layouts import (
    Component,
    Failed,
    Pending,
    Ready,
    ResourceDelivery,
    ResourceNotReadyError,
    computed,
    resource,
    state,
    transaction,
)
from squid_layouts.primitives import Text
from squid_layouts.runtime import ComponentRuntime


class ResourcePanel(Component):
    kind: str = state("first")
    filters: list[str] = state([])
    visible: bool = state(default=True)

    def __init__(self, load: Callable[[str, tuple[str, ...]], Awaitable[str]]) -> None:
        self._load = load

    @resource(depends=(kind, filters))
    async def result(self) -> str:
        return await self._load(self.kind, tuple(self.filters))

    def render(self):
        if not self.visible:
            return Text("hidden")
        match self.result.state:
            case Pending(previous=previous):
                suffix = "" if previous is None else f":{previous.value}"
                return Text(f"pending{suffix}")
            case Failed(error=error):
                return Text(f"failed:{error}")
            case Ready(value=value):
                return Text(value)


async def immediate(kind: str, filters: tuple[str, ...]) -> str:
    return f"{kind}:{','.join(filters)}"


class TestResourceState:
    async def test_reload_moves_from_pending_to_ready(self) -> None:
        panel = ResourcePanel(immediate)
        assert panel.result.state == Pending()
        assert await panel.result.reload() == Ready("first:")
        assert panel.result.value == "first:"

    def test_value_rejects_non_ready_state(self) -> None:
        panel = ResourcePanel(immediate)
        with pytest.raises(ResourceNotReadyError, match=r"ResourcePanel\.result"):
            _ = panel.result.value

    async def test_failure_retains_the_previous_ready_value(self) -> None:
        async def load(kind: str, _filters: tuple[str, ...]) -> str:
            if kind == "broken":
                message = "offline"
                raise RuntimeError(message)
            return kind

        panel = ResourcePanel(load)
        await panel.result.reload()
        panel.kind = "broken"

        failed = await panel.result._settle()

        assert isinstance(failed, Failed)
        assert str(failed.error) == "offline"
        assert failed.previous == Ready("first")

    async def test_replace_supersedes_an_in_flight_completion(self) -> None:
        entered = anyio.Event()
        release = anyio.Event()

        async def load(_kind: str, _filters: tuple[str, ...]) -> str:
            entered.set()
            await release.wait()
            return "late"

        panel = ResourcePanel(load)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(panel.result._settle)
            await entered.wait()
            panel.result.replace("authoritative")
            release.set()

        assert panel.result.state == Ready("authoritative")

    async def test_concurrent_settles_share_one_load(self) -> None:
        entered = anyio.Event()
        release = anyio.Event()
        attempts = 0

        async def load(_kind: str, _filters: tuple[str, ...]) -> str:
            nonlocal attempts
            attempts += 1
            entered.set()
            await release.wait()
            return "shared"

        panel = ResourcePanel(load)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(panel.result._settle)
            await entered.wait()
            tasks.start_soon(panel.result._settle)
            await anyio.sleep(0)
            release.set()

        assert attempts == 1
        assert panel.result.state == Ready("shared")

    async def test_cancellation_leaves_the_resource_pending(self) -> None:
        entered = anyio.Event()

        async def load(_kind: str, _filters: tuple[str, ...]) -> str:
            entered.set()
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        panel = ResourcePanel(load)
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(panel.result._settle)
            await entered.wait()
            tasks.cancel_scope.cancel()

        assert panel.result.state == Pending()


class TestResourceDependencies:
    async def test_a_committed_dependency_write_invalidates_with_the_previous_value(self) -> None:
        panel = ResourcePanel(immediate)
        await panel.result.reload()

        with transaction():
            panel.kind = "second"

        assert panel.result.state == Pending(Ready("first:"))
        assert await panel.result._settle() == Ready("second:")

    async def test_a_rolled_back_dependency_write_does_not_invalidate(self) -> None:
        panel = ResourcePanel(immediate)
        await panel.result.reload()

        with pytest.raises(RuntimeError, match="abort"), transaction():
            panel.kind = "second"
            message = "abort"
            raise RuntimeError(message)

        assert panel.result.state == Ready("first:")

    async def test_an_observed_in_place_write_invalidates(self) -> None:
        panel = ResourcePanel(immediate)
        await panel.result.reload()

        panel.filters.append("new")

        assert panel.result.state == Pending(Ready("first:"))
        assert await panel.result._settle() == Ready("first:new")

    async def test_mutated_invalidates_reference_copied_dependencies(self) -> None:
        class RefPanel(Component):
            values: list[str] = state(copy="ref")

            def __init__(self) -> None:
                self.values = []

            @resource(depends=(values,))
            async def joined(self) -> str:
                return ",".join(self.values)

            def render(self):
                return Text(type(self.joined.state).__name__)

        panel = RefPanel()
        await panel.joined.reload()
        panel.values.append("new")
        panel.mutated("values")

        assert panel.joined.state == Pending(Ready(""))

    def test_dependencies_must_be_state_descriptors_on_the_same_component(self) -> None:
        with pytest.raises(TypeError, match=r"Invalid\.result dependency must be an sl\.state"):

            class Invalid(Component):
                @resource(depends=("kind",))
                async def result(self) -> str:
                    return ""

                def render(self):
                    return Text("")

    async def test_computed_dependency_invalidates_only_when_its_value_changes(self) -> None:
        class ComputedPanel(Component):
            kind: str = state("FIRST")

            @computed(depends=(kind,))
            def normalized_kind(self) -> str:
                return self.kind.casefold()

            @resource(depends=(normalized_kind,))
            async def result(self) -> str:
                return self.normalized_kind

            def render(self):
                return Text(type(self.result.state).__name__)

        panel = ComputedPanel()
        assert await panel.result.reload() == Ready("first")

        panel.kind = "first"
        assert panel.result.state == Ready("first")

        panel.kind = "second"
        assert panel.result.state == Pending(Ready("first"))


class TestResourceObservation:
    def test_render_records_each_observed_resource_once(self) -> None:
        panel = ResourcePanel(immediate)
        tree = ComponentRuntime(panel).render()

        assert tree.resources == (panel.result,)

    def test_a_hidden_resource_is_not_observed_or_loaded(self) -> None:
        panel = ResourcePanel(immediate)
        panel.visible = False

        tree = ComponentRuntime(panel).render()

        assert tree.resources == ()

    def test_delivery_policy_is_part_of_the_bound_resource(self) -> None:
        class AtomicPanel(Component):
            @resource(delivery=ResourceDelivery.ATOMIC)
            async def result(self) -> str:
                return "ready"

            def render(self):
                return Text(type(self.result.state).__name__)

        assert AtomicPanel().result.delivery is ResourceDelivery.ATOMIC


class TestResourceOrdering:
    async def test_an_older_completion_cannot_replace_a_newer_dependency_generation(self) -> None:
        entered = {"old": anyio.Event(), "new": anyio.Event()}
        release = {"old": anyio.Event(), "new": anyio.Event()}

        async def load(kind: str, _filters: tuple[str, ...]) -> str:
            captured = kind
            entered[captured].set()
            await release[captured].wait()
            return captured

        panel = ResourcePanel(load)
        panel.kind = "old"
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(panel.result._settle)
            await entered["old"].wait()
            panel.kind = "new"
            tasks.start_soon(panel.result._settle)
            await entered["new"].wait()
            release["new"].set()
            await anyio.sleep(0)
            release["old"].set()

        assert panel.result.state == Ready("new")

"""Run one asynchronous result through an optional mounted pending state."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import cast, overload

from squid_layouts.discord.delivery import Delivered, Destination, SendResult
from squid_layouts.discord.mount import Mount
from squid_layouts.runtime.component import Component, RenderResult
from squid_reactive.operations import Cancelled, Failed, OperationExecution, Pending, Progress, Succeeded, operation

type Work[ValueT] = Callable[[], Awaitable[ValueT]]
type SuccessRenderer[ValueT] = Callable[[ValueT], RenderResult]
type ErrorRenderer = Callable[[Exception], RenderResult]
type ErrorObserver = Callable[[ManagedError], Awaitable[None]]
type MountFactory = Callable[[Component], Mount]
type ManagedDelivery = SendResult | None


@dataclass(frozen=True, slots=True)
class ManagedError:
    """A failed result and the delivery, if any, used to show its outcome."""

    error: Exception
    delivery: ManagedDelivery


class _Scene(Component):
    """A component containing one already-rendered scene."""

    def __init__(self, scene: RenderResult) -> None:
        self._scene = scene

    def render(self) -> RenderResult:
        return self._scene


class _ManagedResult[ValueT](Component):
    """Expose a callback's pending and terminal states as a component."""

    execution: OperationExecution[ValueT, None]

    def __init__(
        self,
        work: Work[ValueT],
        *,
        initial: RenderResult,
        render_success: SuccessRenderer[ValueT],
        render_error: ErrorRenderer | None,
    ) -> None:
        self._work = work
        self._initial = initial
        self._render_success = render_success
        self._render_error = render_error
        self.execution = self._run.start()

    @property
    def initial(self) -> RenderResult:
        """Return the scene shown while the callback is running."""
        return self._initial

    @property
    def render_error(self) -> ErrorRenderer | None:
        """Return the optional failure renderer."""
        return self._render_error

    def render(self) -> RenderResult:
        """Render the pending or terminal result."""
        match self.execution.status:
            case Pending():
                return self._initial
            case Succeeded(value=value):
                return self._render_success(value)
            case Failed(error=error):
                if self._render_error is None:
                    return self._initial
                return self._render_error(error)
            case Cancelled():
                return self._initial

    @operation(initial=None)
    async def _run(self, _progress: Progress[None]) -> ValueT:
        """Run the callback once the initial scene has been delivered."""
        return await self._work()


def _identity[ValueT](value: ValueT) -> RenderResult:
    return cast(RenderResult, value)


@overload
async def run_managed_result(
    work: Work[RenderResult],
    *,
    destination: Destination,
    make_mount: MountFactory,
    initial: RenderResult | None = None,
    render_success: None = None,
    render_error: ErrorRenderer | None = None,
    on_error: ErrorObserver | None = None,
    dismiss_on_success: bool = False,
) -> RenderResult: ...


@overload
async def run_managed_result[ValueT](
    work: Work[ValueT],
    *,
    destination: Destination,
    make_mount: MountFactory,
    initial: RenderResult | None = None,
    render_success: SuccessRenderer[ValueT],
    render_error: ErrorRenderer | None = None,
    on_error: ErrorObserver | None = None,
    dismiss_on_success: bool = False,
) -> ValueT: ...


async def run_managed_result[ValueT](
    work: Work[ValueT],
    *,
    destination: Destination,
    make_mount: MountFactory,
    initial: RenderResult | None = None,
    render_success: SuccessRenderer[ValueT] | None = None,
    render_error: ErrorRenderer | None = None,
    on_error: ErrorObserver | None = None,
    dismiss_on_success: bool = False,
) -> ValueT:
    """Run one callback, optionally showing its progress through a mounted scene.

    When ``initial`` is supplied, the callback starts after that scene is delivered and its
    result is reconciled into the same mount. Without ``initial``, the callback completes before
    a mount is created, so a caller can avoid showing anything while it runs.

    Exceptions from ``work`` are observed and re-raised. ``render_error`` controls an optional
    scene for the failure, while ``on_error`` is an independent reporting hook. Callers that want
    to suppress an exception should catch it inside ``work`` and return an appropriate value.
    """
    renderer: SuccessRenderer[ValueT] = _identity if render_success is None else render_success
    if initial is None:
        return await _run_without_initial(
            work,
            destination=destination,
            make_mount=make_mount,
            render_success=renderer,
            render_error=render_error,
            on_error=on_error,
            dismiss_on_success=dismiss_on_success,
        )

    component = _ManagedResult(
        work,
        initial=initial,
        render_success=renderer,
        render_error=render_error,
    )
    mount = make_mount(component)
    delivered = await mount.send(destination)
    match component.execution.status:
        case Succeeded(value=value):
            if dismiss_on_success and isinstance(delivered, Delivered):
                await mount.dismiss()
            return value
        case Failed(error=error):
            await _observe_error(on_error, error, delivered)
            raise error
        case Cancelled():
            raise asyncio.CancelledError
        case Pending():
            message = "managed result remained pending after mount settlement"
            raise RuntimeError(message)


async def _run_without_initial[ValueT](
    work: Work[ValueT],
    *,
    destination: Destination,
    make_mount: MountFactory,
    render_success: SuccessRenderer[ValueT],
    render_error: ErrorRenderer | None,
    on_error: ErrorObserver | None,
    dismiss_on_success: bool,
) -> ValueT:
    try:
        value = await work()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        if render_error is None:
            await _observe_error(on_error, error, None)
            raise
        mount = make_mount(_Scene(render_error(error)))
        delivered = await mount.send(destination)
        await _observe_error(on_error, error, delivered)
        raise

    mount = make_mount(_Scene(render_success(value)))
    delivered = await mount.send(destination)
    if dismiss_on_success and isinstance(delivered, Delivered):
        await mount.dismiss()
    return value


async def _observe_error(on_error: ErrorObserver | None, error: Exception, delivery: ManagedDelivery) -> None:
    if on_error is not None:
        await on_error(ManagedError(error, delivery))


__all__ = [
    "ErrorObserver",
    "ErrorRenderer",
    "ManagedDelivery",
    "ManagedError",
    "MountFactory",
    "SuccessRenderer",
    "Work",
    "run_managed_result",
]

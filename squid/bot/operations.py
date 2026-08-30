"""Operation-backed command messages."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import Any, cast, overload

import squid_ui as sl
import squid_ui_discord as sd
from squid.bot.errors import build_error_notice, record_operation_error
from squid.bot.ui import error_node, info_node, tr
from squid_ui.document import DocumentLike

type OperationWork = Callable[
    [sl.operations.ProgressReporter[DocumentLike[sl.ComponentsV2Target] | None], sd.delivery.DeliveryResult],
    Awaitable[DocumentLike[sl.ComponentsV2Target]],
]
_INITIAL_PROGRESS: DocumentLike[sl.ComponentsV2Target] | None = None

type ManagedResultHandler[**P] = Callable[P, Awaitable[DocumentLike[sl.ComponentsV2Target]]]
type ManagedResultCallback[**P] = Callable[P, Coroutine[Any, Any, None]]


async def _command_invocation(args: tuple[object, ...]) -> sd.Invocation:
    if len(args) < 2:
        message = "managed_result requires a bound command callback"
        raise RuntimeError(message)
    invocation = await sd.Invocation.of(cast(sd.InvocationSource, args[1]))
    if sd.current_invocation() is not invocation:
        message = "managed_result requires a resolved ambient invocation"
        raise RuntimeError(message)
    return invocation


def _make_root(
    invocation: sd.Invocation,
) -> Callable[[sl.Component[sl.ComponentsV2Target]], sd.MessageRoot[sl.ComponentsV2Target]]:
    def make_root(component: sl.Component[sl.ComponentsV2Target]) -> sd.MessageRoot[sl.ComponentsV2Target]:
        return invocation.runtime.mount(
            component,
            access=sd.Everyone(),
            localization=invocation.localization,
            timeout=900,
        )

    return make_root


def _render_error(invocation: sd.Invocation, error: Exception) -> DocumentLike[sl.ComponentsV2Target]:
    notice = build_error_notice(error, invocation.localization.locale)
    return error_node(notice.title, notice.detail)


async def _record_error(invocation: sd.Invocation, managed: sd.ManagedError) -> None:
    delivered = managed.delivery
    result = delivered.result if isinstance(delivered, sd.delivery.Delivered) else None
    services = getattr(invocation.client, "services", None)
    await record_operation_error(
        managed.error,
        locale=invocation.localization.locale,
        result=result,
        presented=isinstance(delivered, sd.delivery.Delivered) and delivered.settled,
        reports=getattr(services, "error_reports", None),
    )


class CommandOperation(sl.Component[sl.ComponentsV2Target]):
    """A command effect whose progress and terminal outcome are its rendered state."""

    execution: sl.operations.OperationExecution[
        DocumentLike[sl.ComponentsV2Target], DocumentLike[sl.ComponentsV2Target] | None
    ]

    def __init__(self, invocation: sd.Invocation, work: OperationWork) -> None:
        self._invocation = invocation
        self._work = work
        self._initial = info_node(tr(t"Working"), tr(t"Getting information..."))
        self._result: sd.delivery.DeliveryResult | None = None
        self.execution = self._execute.start()

    @sl.operation(initial=_INITIAL_PROGRESS)
    async def _execute(
        self,
        progress: sl.operations.ProgressReporter[DocumentLike[sl.ComponentsV2Target] | None],
    ) -> DocumentLike[sl.ComponentsV2Target]:
        result = self._result
        if result is None:
            message = "a command operation cannot start before its initial delivery"
            raise RuntimeError(message)

        return await self._work(progress, result)

    def render(self) -> DocumentLike[sl.ComponentsV2Target]:
        match self.execution.status:
            case sl.operations.Pending(progress=progress):
                return self._initial if progress is None else progress
            case sl.operations.Succeeded(value=value):
                return value
            case sl.operations.Failed(error=error):
                return _render_error(self._invocation, error)
            case sl.operations.Cancelled(progress=progress):
                return self._initial if progress is None else progress


@overload
def managed_result[**P](
    callback: ManagedResultHandler[P],
    *,
    dismiss_on_success: bool = False,
) -> ManagedResultCallback[P]: ...


@overload
def managed_result[**P](
    *,
    dismiss_on_success: bool = False,
) -> Callable[[ManagedResultHandler[P]], ManagedResultCallback[P]]: ...


def managed_result[**P](
    callback: ManagedResultHandler[P] | None = None,
    *,
    dismiss_on_success: bool = False,
) -> ManagedResultCallback[P] | Callable[[ManagedResultHandler[P]], ManagedResultCallback[P]]:
    """Manage a command callback whose return value is a rendered terminal layout.

    The decorated callback keeps its Discord-facing parameters and requires the command
    dispatcher to have resolved the ambient invocation. It runs after the deferred progress
    card is delivered, and its returned layout becomes the terminal scene.
    """

    def decorate(handler: ManagedResultHandler[P]) -> ManagedResultCallback[P]:
        @wraps(handler)
        async def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
            invocation = await _command_invocation(args)

            async def work() -> DocumentLike:
                return cast(DocumentLike, await handler(*args, **kwargs))

            async def on_error(error: sd.ManagedError) -> None:
                await _record_error(invocation, error)

            await sd.run_managed_result(
                work,
                message_destination=invocation.destination(),
                make_root=cast(sd.MessageRootFactory, _make_root(invocation)),
                initial=cast(DocumentLike, info_node(tr(t"Working"), tr(t"Getting information..."))),
                render_error=cast(sd.ErrorRenderer, lambda error: _render_error(invocation, error)),
                on_error=on_error,
                dismiss_on_success=dismiss_on_success,
            )

        return invoke

    if callback is not None:
        return decorate(callback)
    return decorate


async def run_command_operation(
    invocation: sd.Invocation,
    work: OperationWork,
    *,
    destination: sd.MessageDestination | None = None,
    dismiss_on_success: bool = False,
) -> None:
    """Deliver and settle one command operation, rethrowing a rendered failure."""
    component = CommandOperation(invocation, work)
    message_root = _make_root(invocation)(component)
    target = invocation.destination() if destination is None else destination

    async def capture(payload: sd.message_payload.MessagePayload) -> sd.delivery.DeliveryResult:
        result = await target(payload)
        component._result = result
        return result

    delivered = await message_root.send(capture)
    match component.execution.status:
        case sl.operations.Succeeded():
            if dismiss_on_success:
                await message_root.dismiss()
        case sl.operations.Failed(error=error):
            await _record_error(invocation, sd.ManagedError(error, delivered))
            raise error
        case sl.operations.Cancelled():
            raise asyncio.CancelledError
        case sl.operations.Pending():
            message = "command operation remained pending after mount settlement"
            raise RuntimeError(message)


__all__ = [
    "CommandOperation",
    "ManagedResultCallback",
    "ManagedResultHandler",
    "OperationWork",
    "managed_result",
    "run_command_operation",
]

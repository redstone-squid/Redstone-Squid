"""Operation-backed command messages."""

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Coroutine
from functools import wraps
from typing import Any, overload

from discord.abc import Messageable
from discord.ext.commands import Context

import squid_layouts as sl
from squid.bot.errors import build_error_presentation, record_operation_error
from squid.bot.i18n import resolve_locale
from squid.bot.ui import create_mount, destination, error_node, info_node
from squid.core.i18n import _, translate
from squid_layouts.runtime.component import RenderResult

type OperationWork = Callable[
    [sl.operations.Progress[RenderResult | None], sl.discord.delivery.DeliveryReceipt],
    Awaitable[RenderResult],
]
_INITIAL_PROGRESS: RenderResult | None = None

type ManagedResultHandler[**P] = Callable[P, Awaitable[RenderResult]]
type ManagedResultCallback[**P] = Callable[P, Coroutine[Any, Any, None]]


class CommandOperation(sl.Component):
    """A command effect whose progress and terminal outcome are its rendered state."""

    execution: sl.operations.OperationExecution[RenderResult, RenderResult | None]

    def __init__(
        self,
        work: OperationWork,
        *,
        initial: RenderResult,
        locale: str | None,
    ) -> None:
        self._work = work
        self._initial = initial
        self._locale = locale
        self._receipt: sl.discord.delivery.DeliveryReceipt | None = None
        self.execution = self._execute.start()

    @sl.operation(initial=_INITIAL_PROGRESS)
    async def _execute(
        self,
        progress: sl.operations.Progress[RenderResult | None],
    ) -> RenderResult:
        receipt = self._receipt
        if receipt is None:
            message = "a command operation cannot start before its initial delivery"
            raise RuntimeError(message)

        return await self._work(progress, receipt)

    def render(self) -> RenderResult:
        match self.execution.status:
            case sl.operations.Pending(progress=progress):
                return self._initial if progress is None else progress
            case sl.operations.Succeeded(value=value):
                return value
            case sl.operations.Failed(error=error):
                presentation = build_error_presentation(error, self._locale)
                return error_node(presentation.title, presentation.detail)
            case sl.operations.Cancelled(progress=progress):
                return self._initial if progress is None else progress


class _ManagedResultComponent(sl.Component):
    """Run one command callback after its initial layout has been delivered."""

    execution: sl.operations.OperationExecution[RenderResult, None]

    def __init__(
        self,
        callback: Callable[..., Awaitable[RenderResult]],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        *,
        initial: RenderResult,
        locale: str | None,
    ) -> None:
        self._callback = callback
        self._args = args
        self._kwargs = kwargs
        self._initial = initial
        self._locale = locale
        self.execution = self._execute.start()

    @sl.operation(initial=None)
    async def _execute(self, _progress: sl.operations.Progress[None]) -> RenderResult:
        """Evaluate the command callback once the mount has committed its initial delivery."""
        return await self._callback(*self._args, **self._kwargs)

    def render(self) -> RenderResult:
        """Render the initial card until the callback supplies its terminal layout."""
        match self.execution.status:
            case sl.operations.Pending():
                return self._initial
            case sl.operations.Succeeded(value=value):
                return value
            case sl.operations.Failed(error=error):
                presentation = build_error_presentation(error, self._locale)
                return error_node(presentation.title, presentation.detail)
            case sl.operations.Cancelled():
                return self._initial


@overload
def managed_result[**P](
    callback: ManagedResultHandler[P],
    *,
    title: str = _("Working"),
    description: str = _("Getting information..."),
    dismiss_on_success: bool = False,
) -> ManagedResultCallback[P]: ...


@overload
def managed_result[**P](
    *,
    title: str = _("Working"),
    description: str = _("Getting information..."),
    dismiss_on_success: bool = False,
) -> Callable[[ManagedResultHandler[P]], ManagedResultCallback[P]]: ...


def managed_result[**P](
    callback: ManagedResultHandler[P] | None = None,
    *,
    title: str = _("Working"),
    description: str = _("Getting information..."),
    dismiss_on_success: bool = False,
) -> ManagedResultCallback[P] | Callable[[ManagedResultHandler[P]], ManagedResultCallback[P]]:
    """Manage a command callback whose return value is a rendered terminal layout.

    The decorated callback keeps its Discord-facing parameters. It runs after the initial
    progress card is delivered, and its returned layout becomes the terminal scene. Failures
    are rendered, recorded, and re-raised through the command's normal error handling path.
    """

    def decorate(handler: ManagedResultHandler[P]) -> ManagedResultCallback[P]:
        @wraps(handler)
        async def invoke(*args: P.args, **kwargs: P.kwargs) -> None:
            bound = inspect.signature(handler).bind(*args, **kwargs)
            ctx = _command_context(bound.arguments)
            locale = await _command_locale(ctx, bound.arguments)
            component = _ManagedResultComponent(
                handler,
                args,
                dict(kwargs),
                initial=info_node(translate(locale, title), translate(locale, description)),
                locale=locale,
            )
            mount = create_mount(component, source=ctx, access=sl.discord.Everyone(), locale=locale, timeout=900)
            delivered = await mount.send(destination(ctx, locale=locale))
            match component.execution.status:
                case sl.operations.Succeeded():
                    if dismiss_on_success:
                        await mount.dismiss()
                case sl.operations.Failed(error=error):
                    receipt = delivered.receipt if isinstance(delivered, sl.discord.delivery.Delivered) else None
                    await record_operation_error(
                        error,
                        locale=locale,
                        receipt=receipt,
                        presented=isinstance(delivered, sl.discord.delivery.Delivered) and delivered.settled,
                        reports=_error_reports(bound.arguments),
                    )
                    raise error
                case sl.operations.Cancelled():
                    raise asyncio.CancelledError
                case sl.operations.Pending():
                    message = "managed command result remained pending after mount settlement"
                    raise RuntimeError(message)

        return invoke

    if callback is not None:
        return decorate(callback)
    return decorate


def _command_context(arguments: dict[str, object]) -> Context[Any]:
    """Find the command context in a bound discord.py callback."""
    for name in ("ctx", "context"):
        value = arguments.get(name)
        if value is not None:
            return value  # type: ignore[return-value]
    message = "managed_result requires a command callback with a ctx or context parameter"
    raise TypeError(message)


async def _command_locale(ctx: Context[Any], arguments: dict[str, object]) -> str | None:
    """Resolve the command locale when the owning bot exposes its settings service."""
    settings = next((getattr(candidate, "settings_service", None) for candidate in arguments.values()), None)
    if settings is None:
        for candidate in arguments.values():
            services = getattr(candidate, "services", None)
            settings = getattr(services, "settings", None)
            if settings is not None:
                break
    if settings is None:
        return None
    return await resolve_locale(ctx, settings)


def _error_reports(arguments: dict[str, object]) -> Any:
    """Find the bot's error report service without coupling the decorator to a cog type."""
    for candidate in arguments.values():
        services = getattr(candidate, "services", None)
        reports = getattr(services, "error_reports", None)
        if reports is not None:
            return reports
    return None


async def run_command_operation(
    target: Messageable,
    work: OperationWork,
    *,
    source: sl.discord.host.HostSource,
    title: str = _("Working"),
    description: str = _("Getting information..."),
    locale: str | None = None,
    dismiss_on_success: bool = False,
    reports: Any = None,
) -> None:
    """Deliver and settle one command operation, rethrowing a rendered failure."""
    component = CommandOperation(
        work,
        initial=info_node(translate(locale, title), translate(locale, description)),
        locale=locale,
    )
    mount = create_mount(component, source=source, access=sl.discord.Everyone(), locale=locale, timeout=900)
    destination = sl.discord.send_to(target)

    async def capture(
        presentation: sl.discord.presentation.DiscordPresentation,
    ) -> sl.discord.delivery.DeliveryReceipt:
        receipt = await destination(presentation)
        component._receipt = receipt
        return receipt

    delivered = await mount.send(capture)
    match component.execution.status:
        case sl.operations.Succeeded():
            if dismiss_on_success:
                await mount.dismiss()
        case sl.operations.Failed(error=error):
            receipt = delivered.receipt if isinstance(delivered, sl.discord.delivery.Delivered) else None
            await record_operation_error(
                error,
                locale=locale,
                receipt=receipt,
                presented=isinstance(delivered, sl.discord.delivery.Delivered) and delivered.settled,
                reports=reports,
            )
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

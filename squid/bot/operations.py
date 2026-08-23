"""Operation-backed command messages."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from discord import Webhook
from discord.abc import Messageable

import squid_layouts as sl
from squid.bot.errors import build_error_presentation, record_operation_error
from squid.bot.ui import create_mount, error_node, info_node, send_to
from squid.core.i18n import _, translate
from squid_layouts.runtime.component import RenderResult

type OperationWork = Callable[
    [sl.operations.Progress[RenderResult | None], sl.discord.delivery.DeliveryReceipt],
    Awaitable[RenderResult],
]
_INITIAL_PROGRESS: RenderResult | None = None


class CommandOperation(sl.Component):
    """A command effect whose progress and terminal outcome are its rendered state."""

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

    @sl.operation(initial=_INITIAL_PROGRESS)
    async def execution(
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


async def run_command_operation(
    target: Messageable | Webhook,
    work: OperationWork,
    *,
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
    mount = create_mount(component, access=sl.discord.Everyone(), locale=locale, timeout=900)
    destination = send_to(target)

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


__all__ = ["CommandOperation", "OperationWork", "run_command_operation"]

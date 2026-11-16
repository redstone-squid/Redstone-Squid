"""Deliver persistent ui: addresses to a portable router without another route table."""

import re
import weakref
from typing import Any, Self, override

import discord
from squid_ui.content.text import tr
from squid_ui.ext.discord.messages.contracts import ErrorHook
from squid_ui.ext.discord.request import Request, request
from squid_ui.ext.discord.routing._dispatch import handle_error
from squid_ui.factories import paragraph
from squid_ui.profiling import NoOpProfiler, OperationKind, Profiler, TraceResult, TraceStatus
from squid_ui.routing import InvalidAddress, Router

_INSTALLED: weakref.WeakKeyDictionary[discord.Client, Router[Request[Any, Any]]] = weakref.WeakKeyDictionary()
_NOOP_PROFILER = NoOpProfiler()


def installed_router(client: discord.Client) -> Router[Request[Any, Any]] | None:
    """Return the UI router attached to this client's persistent address adapter."""
    return _INSTALLED.get(client)


def install[Bot: discord.Client](
    router: Router[Request[Any, Bot]], client: Bot, *, on_error: ErrorHook, profiler: Profiler = _NOOP_PROFILER
) -> None:
    """Install one address dispatcher after endpoint modules have been imported."""
    if existing := _INSTALLED.get(client):
        if existing is not router:
            message = "This client already has a UI router"
            raise ValueError(message)
        return
    router.freeze()

    class AddressControl(discord.ui.DynamicItem[discord.ui.Item[Any]], template=r"ui:.*"):
        @classmethod
        @override
        async def from_custom_id(
            cls, interaction: discord.Interaction[Any], item: discord.ui.Item[Any], match: re.Match[str], /
        ) -> Self:
            return cls(item)

        @override
        async def callback(self, interaction: discord.Interaction[Any]) -> None:
            context = await request(interaction)
            try:
                match = router.resolve(self.custom_id.removeprefix("ui:"))
            except InvalidAddress:
                await context.respond(
                    paragraph(tr(t"ui-control-expired = This control is no longer available.")), audience="personal"
                )
                return
            with profiler.operation(OperationKind.ROUTE_DISPATCH, name=match.endpoint.path) as profile:
                try:
                    await router.dispatch(context, match)
                    if not interaction.response.is_done():
                        await context.defer()
                except Exception as error:
                    profile.set_result(TraceResult(TraceStatus.FAILED, type(error).__qualname__))
                    await handle_error(interaction, error, f"ui:{match.endpoint.path}", on_error=on_error)
                else:
                    profile.set_result(TraceResult(TraceStatus.COMPLETED))

    client.add_dynamic_items(AddressControl)
    _INSTALLED[client] = router

"""Dispatch persistent UI controls through the interaction's runtime."""

import re
from typing import Any, Self, override

import discord
from squid_ui.content.text import plain
from squid_ui.ext.discord.request import request
from squid_ui.factories import paragraph
from squid_ui.profiling import NoOpProfiler, OperationKind, TraceResult, TraceStatus
from squid_ui.routing import InvalidAddress

_NOOP_PROFILER = NoOpProfiler()


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
        runtime = context.runtime
        router = runtime.routes
        assert router is not None
        try:
            match = router.resolve(self.custom_id.removeprefix("ui:"))
        except InvalidAddress:
            await context.respond(paragraph(plain("This control is no longer available.")), audience="personal")
            return
        with (runtime.config.profiler or _NOOP_PROFILER).operation(
            OperationKind.ROUTE_DISPATCH, name=match.endpoint.path
        ) as profile:
            try:
                await router.dispatch(context, match)
                if not interaction.response.is_done():
                    await context.defer()
            except Exception as error:
                profile.set_result(TraceResult(TraceStatus.FAILED, type(error).__qualname__))
                await runtime.handle_request_error(context, error, f"ui:{match.endpoint.path}")
            else:
                profile.set_result(TraceResult(TraceStatus.COMPLETED))

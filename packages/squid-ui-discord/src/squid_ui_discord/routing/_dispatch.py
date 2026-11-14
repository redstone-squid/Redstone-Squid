"""Execute a resolved interaction with middleware, response deadlines, and error handling.

Each execution uses a snapshot of its handler and middleware, so reloads affect
subsequent interactions without changing work already in progress.
"""

import logging
from typing import Any

import anyio
import discord

from squid_ui.profiling import OperationKind, OperationRecorder, Profiler, TraceResult, TraceStatus
from squid_ui.text import NEUTRAL, localization_scope
from squid_ui_discord.message_root_contracts import ErrorHook
from squid_ui_discord.request import request as resolve_request
from squid_ui_discord.runtime import DiscordUIRuntimeMissing

from ._contracts import (
    Middleware,
    RouteProceed,
    RouteRequest,
)

logger = logging.getLogger("squid_ui_discord.routing")


async def execute[BotT: discord.Client](
    request: RouteRequest[BotT],
    operation: RouteProceed,
    middleware: tuple[Middleware[BotT], ...],
    *,
    source: str,
    profiler: Profiler,
    on_error: ErrorHook | None,
    acknowledgement_timeout: float,
) -> None:
    """Run a resolved request with localization, profiling, and an acknowledgement watchdog."""
    interaction = request.interaction
    trace_name = request.route.format if request.route is not None else source
    with profiler.operation(
        OperationKind.ROUTE_DISPATCH,
        name=trace_name,
        attributes={
            "component": request.component.value,
            "matched_alias": request.matched_alias,
            "middleware": len(middleware),
            "actor": request.interaction.user.id,
        },
    ) as profile:
        acknowledgement = profile.start_span("acknowledgement")

        def finish_acknowledgement(source: str) -> None:
            if interaction.response.is_done():
                acknowledgement.set_attribute("source", source)
                acknowledgement.finish()

        async def endpoint() -> None:
            with profile.span("handler"):
                await operation()
            finish_acknowledgement("handler")

        async def dispatch_operation() -> None:
            try:
                localization = (await resolve_request(interaction)).localization
            except DiscordUIRuntimeMissing:
                localization = NEUTRAL
            try:
                with localization_scope(localization):
                    handled = await run_middleware(middleware, request, endpoint, profile=profile)
            except Exception as error:
                profile.set_result(
                    TraceResult(TraceStatus.FAILED, f"{type(error).__module__}.{type(error).__qualname__}")
                )
                with localization_scope(localization), profile.span("error_hook"):
                    await handle_error(interaction, error, source, on_error=on_error)
                finish_acknowledgement("error_hook")
            else:
                profile.set_result(TraceResult(TraceStatus.COMPLETED, None if handled else "short_circuited"))

        async def watchdog() -> None:
            await anyio.sleep(acknowledgement_timeout)
            deferred = await acknowledge_safely(interaction, source)
            finish_acknowledgement("watchdog" if deferred else "handler")
            if deferred:
                profile.mark_deadline_missed()

        async with anyio.create_task_group() as tasks:
            tasks.start_soon(watchdog)
            try:
                await dispatch_operation()
            finally:
                tasks.cancel_scope.cancel()
        deferred = await acknowledge_safely(interaction, source)
        finish_acknowledgement("final_defer" if deferred else "handler")


async def run_middleware[BotT: discord.Client](
    middleware: tuple[Middleware[BotT], ...],
    request: RouteRequest[BotT],
    endpoint: RouteProceed,
    *,
    profile: OperationRecorder,
) -> bool:
    """Compose a one-shot middleware chain in first-added, outermost order."""
    handled = False

    async def invoke(index: int) -> None:
        nonlocal handled
        if index == len(middleware):
            handled = True
            await endpoint()
            return

        active = True
        called = False

        async def proceed() -> None:
            nonlocal called
            if not active:
                message = "route middleware proceed() is only valid during dispatch()"
                raise RuntimeError(message)
            if called:
                message = "route middleware proceed() may only be called once"
                raise RuntimeError(message)
            called = True
            await invoke(index + 1)

        try:
            kind = type(middleware[index])
            with profile.span(f"middleware:{kind.__module__}.{kind.__qualname__}"):
                await middleware[index].dispatch(request, proceed)
        finally:
            active = False

    await invoke(0)
    return handled


async def handle_error(
    interaction: discord.Interaction[Any],
    error: Exception,
    source: str,
    *,
    on_error: ErrorHook | None,
) -> None:
    """Present one dispatch failure without allowing the error hook to escape."""
    if on_error is None:
        logger.error("routed handler failed in %s", source, exc_info=error)
        return
    try:
        await on_error(interaction, error, source)
    except Exception:
        logger.exception("routed error hook failed in %s", source)


async def acknowledge_safely(interaction: discord.Interaction[Any], source: str) -> bool:
    """Acknowledge an unused response slot, tolerating a concurrent handler response."""
    if interaction.response.is_done():
        return False
    try:
        await interaction.response.defer()
    except discord.InteractionResponded:
        return False
    except discord.HTTPException as error:
        # The handler may reach Discord before discord.py records its response.
        if error.code != 40060:
            logger.exception("could not acknowledge %s", source)
        return False
    except Exception:
        logger.exception("could not acknowledge %s", source)
        return False
    return True

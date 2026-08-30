"""Request-injecting command, context-menu, and autocomplete decorators."""

import annotationlib
import inspect
import weakref
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from dataclasses import dataclass
from functools import wraps
from typing import Concatenate, cast, get_origin

import discord
from discord import app_commands
from discord.ext import commands

from squid_ui.forms import FormSpec
from squid_ui_discord.contracts import RequestSource
from squid_ui_discord.ext.contracts import (
    AsyncHandler,
    AsyncHandlerTransform,
    AutocompleteItem,
    ChoiceValue,
    CommandResult,
)
from squid_ui_discord.facade import DiscordUI
from squid_ui_discord.modal import ModalSpec
from squid_ui_discord.request import AcknowledgementPolicy, DiscordRequest
from squid_ui_discord.response import Abandoned, Presented, Rejected, Response, ResponseResult, Sent
from squid_ui_discord.runtime import DiscordUIRuntime

_OUTCOMES = (Sent, Presented, Rejected, Abandoned)


@dataclass(frozen=True, slots=True)
class ContextMenuDeclaration:
    """One immutable context-menu declaration attached outside the callback object."""

    name: str
    type: discord.AppCommandType
    acknowledgement: AcknowledgementPolicy


_CONTEXT_MENUS: weakref.WeakKeyDictionary[AsyncHandler, ContextMenuDeclaration] = weakref.WeakKeyDictionary()


def _source_parameter(callback: Callable[..., object]) -> tuple[list[inspect.Parameter], int]:
    signature = inspect.signature(callback, annotation_format=annotationlib.Format.FORWARDREF)
    parameters = list(signature.parameters.values())
    source_index = 1 if parameters and parameters[0].name in {"self", "cls"} else 0
    if source_index >= len(parameters):
        message = "a Squid command needs DiscordRequest in the native source slot"
        raise TypeError(message)
    parameter = parameters[source_index]
    if get_origin(parameter.annotation) is not DiscordRequest and parameter.annotation is not DiscordRequest:
        message = "DiscordRequest is only supported in the native source slot"
        raise TypeError(message)
    return parameters, source_index


def _external_signature(callback: Callable[..., object]) -> inspect.Signature:
    parameters, source_index = _source_parameter(callback)
    parameters[source_index] = parameters[source_index].replace(annotation=discord.Interaction)
    return inspect.Signature(parameters, return_annotation=inspect.Signature.empty)


def _as_request_source(value: object) -> RequestSource:
    if isinstance(value, discord.Interaction | commands.Context):
        return value
    if hasattr(value, "response") and hasattr(value, "followup") and hasattr(value, "user"):
        return cast(RequestSource, value)
    if callable(getattr(value, "send", None)) and hasattr(value, "author"):
        return cast(RequestSource, value)
    message = "discord.py supplied neither an Interaction nor a Context"
    raise TypeError(message)


def _ui_for[OwnerT](owner: OwnerT, source: RequestSource) -> DiscordUI[OwnerT]:
    ui = getattr(owner, "ui", None)
    if isinstance(ui, DiscordUI):
        return cast(DiscordUI[OwnerT], ui)
    if isinstance(ui, DiscordUIRuntime):
        return ui.scope(owner)
    app_ui = getattr(owner, "app_ui", None)
    if isinstance(app_ui, DiscordUI):
        return cast(DiscordUI[OwnerT], app_ui)
    return DiscordUIRuntime.of(source).scope(owner)


async def present_return[OwnerT, SourceT: RequestSource](
    request: DiscordRequest[OwnerT, SourceT],
    result: CommandResult,
) -> ResponseResult | None:
    """Present one supported handler return through its request."""
    if result is None or isinstance(result, _OUTCOMES):
        return result
    if request.responded:
        message = "a handler explicitly responded and also returned response content"
        raise RuntimeError(message)
    if isinstance(result, FormSpec | ModalSpec | discord.ui.Modal):
        if request.acknowledgement != "form":
            message = "returning a form requires acknowledgement='form'"
            raise RuntimeError(message)
        await request.open_form(result)
        return None
    if isinstance(result, Response):
        return await request.respond(result)
    return await request.respond(result)


def command[CommandOwnerT, **CommandP, CommandReturnT: CommandResult](
    *, acknowledgement: AcknowledgementPolicy = "none"
) -> Callable[
    [Callable[Concatenate[CommandOwnerT, DiscordRequest[CommandOwnerT], CommandP], Awaitable[CommandReturnT]]],
    Callable[
        Concatenate[CommandOwnerT, discord.Interaction[discord.Client], CommandP],
        Coroutine[None, None, None],
    ],
]:
    """Inject ``DiscordRequest`` into a command's native source slot."""

    def decorate(
        callback: Callable[
            Concatenate[CommandOwnerT, DiscordRequest[CommandOwnerT], CommandP],
            Awaitable[CommandReturnT],
        ],
    ) -> Callable[
        Concatenate[CommandOwnerT, discord.Interaction[discord.Client], CommandP],
        Coroutine[None, None, None],
    ]:
        if not inspect.iscoroutinefunction(callback):
            message = "place @sdx.command directly beneath the native command decorator"
            raise TypeError(message)
        dynamic_callback = cast(AsyncHandler, callback)
        _, source_index = _source_parameter(callback)
        method = source_index == 1

        @wraps(callback)
        async def outward(*args: object, **kwargs: object) -> None:
            if len(args) <= source_index:
                message = "discord.py did not provide the command source"
                raise TypeError(message)
            source = _as_request_source(args[source_index])
            owner = args[0] if method else DiscordUIRuntime.of(source).client
            request = await DiscordRequest.create(
                _ui_for(owner, source),
                source,
                acknowledgement=acknowledgement,
            )
            if acknowledgement in ("private", "public"):
                await request.defer(acknowledgement)
            injected = [*args]
            injected[source_index] = request
            result = await dynamic_callback(*injected, **kwargs)
            await present_return(request, cast(CommandResult, result))

        outward.__signature__ = _external_signature(callback)  # type: ignore[attr-defined]
        return cast(
            Callable[
                Concatenate[CommandOwnerT, discord.Interaction[discord.Client], CommandP],
                Coroutine[None, None, None],
            ],
            outward,
        )

    return decorate


def autocomplete[AutocompleteOwnerT, **AutocompleteP]() -> Callable[
    [
        Callable[
            Concatenate[AutocompleteOwnerT, DiscordRequest[AutocompleteOwnerT], AutocompleteP],
            Awaitable[Sequence[AutocompleteItem]],
        ]
    ],
    Callable[
        Concatenate[AutocompleteOwnerT, discord.Interaction[discord.Client], AutocompleteP],
        Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
    ],
]:
    """Inject a request and normalize autocomplete choices to Discord's limit."""

    def decorate(
        callback: Callable[
            Concatenate[AutocompleteOwnerT, DiscordRequest[AutocompleteOwnerT], AutocompleteP],
            Awaitable[Sequence[AutocompleteItem]],
        ],
    ) -> Callable[
        Concatenate[AutocompleteOwnerT, discord.Interaction[discord.Client], AutocompleteP],
        Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
    ]:
        dynamic_callback = cast(Callable[..., Awaitable[Sequence[AutocompleteItem]]], callback)
        _, source_index = _source_parameter(callback)
        method = source_index == 1

        @wraps(callback)
        async def outward(*args: object, **kwargs: object) -> list[app_commands.Choice[ChoiceValue]]:
            source = _as_request_source(args[source_index])
            owner = args[0] if method else DiscordUIRuntime.of(source).client
            request = await DiscordRequest.create(_ui_for(owner, source), source)
            injected = [*args]
            injected[source_index] = request
            values = await dynamic_callback(*injected, **kwargs)
            if not isinstance(values, Sequence) or isinstance(values, str | bytes):
                message = "autocomplete must return choices or (label, value) pairs"
                raise TypeError(message)
            choices: list[app_commands.Choice[ChoiceValue]] = []
            for value in values[:25]:
                if isinstance(value, app_commands.Choice):
                    choice = value
                elif isinstance(value, tuple) and len(value) == 2:
                    label, raw = value
                    if not isinstance(label, str) or not isinstance(raw, str | int | float):
                        message = "autocomplete pairs must contain a string label and scalar value"
                        raise TypeError(message)
                    choice = app_commands.Choice(name=label, value=raw)
                else:
                    message = "autocomplete must return choices or (label, value) pairs"
                    raise TypeError(message)
                if len(choice.name) > 100:
                    message = "autocomplete choice labels are limited to 100 characters"
                    raise ValueError(message)
                choices.append(cast(app_commands.Choice[ChoiceValue], choice))
            return choices

        outward.__signature__ = _external_signature(callback)  # type: ignore[attr-defined]
        return cast(
            Callable[
                Concatenate[AutocompleteOwnerT, discord.Interaction[discord.Client], AutocompleteP],
                Coroutine[None, None, list[app_commands.Choice[ChoiceValue]]],
            ],
            outward,
        )

    return decorate


def context_menu(
    *,
    name: str,
    type: discord.AppCommandType = discord.AppCommandType.message,
    acknowledgement: AcknowledgementPolicy = "none",
) -> AsyncHandlerTransform:
    """Declare a cog-bound context menu converted during cog loading."""
    if type not in (discord.AppCommandType.message, discord.AppCommandType.user):
        message = "context menus must target messages or users"
        raise ValueError(message)

    def decorate[**HandlerP, HandlerReturnT](
        callback: Callable[HandlerP, Awaitable[HandlerReturnT]],
    ) -> Callable[HandlerP, Awaitable[HandlerReturnT]]:
        _source_parameter(callback)
        _CONTEXT_MENUS[cast(AsyncHandler, callback)] = ContextMenuDeclaration(name, type, acknowledgement)
        return callback

    return decorate


def context_menu_declaration(callback: AsyncHandler) -> ContextMenuDeclaration | None:
    """Return a callback's context-menu declaration, if it has one."""
    try:
        return _CONTEXT_MENUS.get(callback)
    except TypeError:
        return None


__all__ = ["autocomplete", "command", "context_menu", "context_menu_declaration", "present_return"]

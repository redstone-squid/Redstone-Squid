"""Discord event aliases with a Request as their invocation context.

The ``on_*`` helpers mark a callback as the Discord implementation of a portable handler.
Frontend selection then chooses it when rendering for Discord, and the callback can use
``event.context`` for Discord-specific request information.
"""

from collections.abc import Awaitable, Callable
from typing import Any, cast

import discord

from squid_ui import interactions, semantic
from squid_ui.handlers import FrontendHandler
from squid_ui_discord.request import Request

type ActionEvent[ClientT: discord.Client = discord.Client] = interactions.ActionEvent[Request[Any, ClientT]]
type PressEvent[ClientT: discord.Client = discord.Client] = interactions.PressEvent[Request[Any, ClientT]]
type SelectionEvent[ClientT: discord.Client = discord.Client] = interactions.SelectionEvent[Request[Any, ClientT]]
type EntitySelectionEvent[ClientT: discord.Client = discord.Client] = interactions.EntitySelectionEvent[
    Request[Any, ClientT]
]
type SubmitEvent[ClientT: discord.Client = discord.Client] = interactions.SubmitEvent[Request[Any, ClientT]]
type InvalidSubmitEvent[ClientT: discord.Client = discord.Client] = interactions.InvalidSubmitEvent[
    Request[Any, ClientT]
]
type ChoiceEvent[ClientT: discord.Client = discord.Client] = semantic.ChoiceEvent[Request[Any, ClientT]]
type EntityEvent[ClientT: discord.Client = discord.Client] = semantic.EntityEvent[Request[Any, ClientT]]
type ToggleEvent[ClientT: discord.Client = discord.Client] = semantic.ToggleEvent[Request[Any, ClientT]]
type NavigateEvent[ClientT: discord.Client = discord.Client] = semantic.NavigateEvent[Request[Any, ClientT]]
type ScaleEvent[ClientT: discord.Client = discord.Client] = semantic.ScaleEvent[Request[Any, ClientT]]
type OpenEvent[ValueT, ClientT: discord.Client = discord.Client] = semantic.OpenEvent[ValueT, Request[Any, ClientT]]


def _register[EventT: interactions.ActionEvent](
    callback: Callable[[EventT], Awaitable[None]],
) -> FrontendHandler[Any]:
    async def invoke(event: interactions.ActionEvent) -> None:
        if not isinstance(event.context, Request):
            message = "a Discord callback requires an installed request context"
            raise TypeError(message)
        # Public registration functions pair each portable shape with its Discord alias.
        await callback(cast(EventT, event))

    return FrontendHandler("discord", invoke)


def on_press[ClientT: discord.Client](
    callback: Callable[[PressEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[interactions.PressEvent]:
    """Register a Discord press implementation for frontend selection."""
    return _register(callback)


def on_selection[ClientT: discord.Client](
    callback: Callable[[SelectionEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[interactions.SelectionEvent]:
    """Register a Discord selection implementation for frontend selection."""
    return _register(callback)


def on_entity_selection[ClientT: discord.Client](
    callback: Callable[[EntitySelectionEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[interactions.EntitySelectionEvent]:
    """Register a Discord entity selection implementation for frontend selection."""
    return _register(callback)


def on_submit[ClientT: discord.Client](
    callback: Callable[[SubmitEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[interactions.SubmitEvent]:
    """Register a Discord submit implementation for frontend selection."""
    return _register(callback)


def on_invalid_submit[ClientT: discord.Client](
    callback: Callable[[InvalidSubmitEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[interactions.InvalidSubmitEvent]:
    """Register a Discord invalid submit implementation for frontend selection."""
    return _register(callback)


def on_choice[ClientT: discord.Client](
    callback: Callable[[ChoiceEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[semantic.ChoiceEvent]:
    """Register a Discord choice implementation for frontend selection."""
    return _register(callback)


def on_entity[ClientT: discord.Client](
    callback: Callable[[EntityEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[semantic.EntityEvent]:
    """Register a Discord entity implementation for frontend selection."""
    return _register(callback)


def on_toggle[ClientT: discord.Client](
    callback: Callable[[ToggleEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[semantic.ToggleEvent]:
    """Register a Discord toggle implementation for frontend selection."""
    return _register(callback)


def on_navigate[ClientT: discord.Client](
    callback: Callable[[NavigateEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[semantic.NavigateEvent]:
    """Register a Discord navigate implementation for frontend selection."""
    return _register(callback)


def on_scale[ClientT: discord.Client](
    callback: Callable[[ScaleEvent[ClientT]], Awaitable[None]],
) -> FrontendHandler[semantic.ScaleEvent]:
    """Register a Discord scale implementation for frontend selection."""
    return _register(callback)

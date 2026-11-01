"""Discord event aliases with a request as their invocation context."""

from collections.abc import Awaitable, Callable
from typing import Any, cast

from squid_ui import interactions, semantic
from squid_ui.handlers import FrontendHandler
from squid_ui_discord.request import Request

type ActionEvent = interactions.ActionEvent[Request]
type PressEvent = interactions.PressEvent[Request]
type SelectionEvent = interactions.SelectionEvent[Request]
type EntitySelectionEvent = interactions.EntitySelectionEvent[Request]
type SubmitEvent = interactions.SubmitEvent[Request]
type InvalidSubmitEvent = interactions.InvalidSubmitEvent[Request]
type ChoiceEvent = semantic.ChoiceEvent[Request]
type EntityEvent = semantic.EntityEvent[Request]
type ToggleEvent = semantic.ToggleEvent[Request]
type NavigateEvent = semantic.NavigateEvent[Request]
type ScaleEvent = semantic.ScaleEvent[Request]
type OpenEvent[ValueT] = semantic.OpenEvent[ValueT, Request]


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


def on_press(callback: Callable[[PressEvent], Awaitable[None]]) -> FrontendHandler[interactions.PressEvent]:
    """Register a Discord press implementation for frontend selection."""
    return _register(callback)


def on_selection(callback: Callable[[SelectionEvent], Awaitable[None]]) -> FrontendHandler[interactions.SelectionEvent]:
    """Register a Discord selection implementation for frontend selection."""
    return _register(callback)


def on_entity_selection(
    callback: Callable[[EntitySelectionEvent], Awaitable[None]],
) -> FrontendHandler[interactions.EntitySelectionEvent]:
    """Register a Discord entity selection implementation for frontend selection."""
    return _register(callback)


def on_submit(callback: Callable[[SubmitEvent], Awaitable[None]]) -> FrontendHandler[interactions.SubmitEvent]:
    """Register a Discord submit implementation for frontend selection."""
    return _register(callback)


def on_invalid_submit(
    callback: Callable[[InvalidSubmitEvent], Awaitable[None]],
) -> FrontendHandler[interactions.InvalidSubmitEvent]:
    """Register a Discord invalid submit implementation for frontend selection."""
    return _register(callback)


def on_choice(callback: Callable[[ChoiceEvent], Awaitable[None]]) -> FrontendHandler[semantic.ChoiceEvent]:
    """Register a Discord choice implementation for frontend selection."""
    return _register(callback)


def on_entity(callback: Callable[[EntityEvent], Awaitable[None]]) -> FrontendHandler[semantic.EntityEvent]:
    """Register a Discord entity implementation for frontend selection."""
    return _register(callback)


def on_toggle(callback: Callable[[ToggleEvent], Awaitable[None]]) -> FrontendHandler[semantic.ToggleEvent]:
    """Register a Discord toggle implementation for frontend selection."""
    return _register(callback)


def on_navigate(callback: Callable[[NavigateEvent], Awaitable[None]]) -> FrontendHandler[semantic.NavigateEvent]:
    """Register a Discord navigate implementation for frontend selection."""
    return _register(callback)


def on_scale(callback: Callable[[ScaleEvent], Awaitable[None]]) -> FrontendHandler[semantic.ScaleEvent]:
    """Register a Discord scale implementation for frontend selection."""
    return _register(callback)

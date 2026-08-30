"""Static contracts for composable discord.py integration."""

from collections.abc import Awaitable, Callable
from typing import Protocol

import discord
from discord import app_commands

from squid_ui.forms import FormSpec
from squid_ui_discord.contracts import FacadeContent
from squid_ui_discord.modal import ModalSpec
from squid_ui_discord.response import Response, ResponseResult

type CommandResult = FacadeContent | Response | FormSpec | ModalSpec | discord.ui.Modal | ResponseResult | None
type ChoiceValue = str | int | float
type AutocompleteItem = app_commands.Choice[ChoiceValue] | tuple[str, ChoiceValue]
type AsyncHandler = Callable[..., Awaitable[object]]


class AsyncDecorator(Protocol):
    """A decorator that preserves an async handler's signature."""

    def __call__[**P, T](self, handler: Callable[P, Awaitable[T]], /) -> Callable[P, Awaitable[T]]: ...


__all__ = [
    "AsyncDecorator",
    "AsyncHandler",
    "AutocompleteItem",
    "ChoiceValue",
    "CommandResult",
]

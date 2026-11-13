"""Presentation metadata for slash command parameters."""

from collections.abc import Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from discord import app_commands

from squid_i18n import Text
from squid_ui_discord.i18n import to_locale_str

type CommandText = str | app_commands.locale_str | Text
type AutocompleteCallback = Callable[..., Coroutine[Any, Any, list[app_commands.Choice[Any]]]]


def command_text(value: CommandText) -> str | app_commands.locale_str:
    return to_locale_str(value) if isinstance(value, Text) else value


@dataclass(frozen=True, slots=True)
class Choice:
    """A stable option value and its plain or translated display name."""

    value: str | int | float
    name: CommandText


@dataclass(frozen=True, slots=True, kw_only=True)
class Option:
    """A parameter's Discord metadata; its type and default stay in the handler signature.

    Names and descriptions accept plain strings, locale_str, or parameter-free Fluent messages.
    Autocomplete accepts a native Discord callback, including one adapted by sd.autocomplete.
    Choices and autocomplete are mutually exclusive. Omitted fields preserve native decorators.
    """

    name: CommandText | None = None
    description: CommandText | None = None
    choices: Sequence[Choice | app_commands.Choice[Any]] | None = None
    autocomplete: AutocompleteCallback | None = None


def apply_options(callback: Callable[..., object], options: Mapping[str, Option], parameter_names: set[str]) -> None:
    """Attach metadata before discord.py constructs and localizes the command."""
    for name, option in options.items():
        if name not in parameter_names:
            message = f"Unknown command option {name!r}"
            raise TypeError(message)
        if option.name is not None:
            app_commands.rename(**{name: command_text(option.name)})(callback)
        if option.description is not None:
            app_commands.describe(**{name: command_text(option.description)})(callback)
        if option.choices is not None:
            choices = [
                app_commands.Choice(name=command_text(choice.name), value=choice.value)
                if isinstance(choice, Choice)
                else choice
                for choice in option.choices
            ]
            app_commands.choices(**{name: choices})(callback)
        if option.autocomplete is not None:
            app_commands.autocomplete(**{name: option.autocomplete})(callback)

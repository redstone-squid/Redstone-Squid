"""Every autocompleted command parameter must name a registered source.

A source id is a string written at the call site, so a typo or a renamed source produces a
dropdown that is silently always empty — the failure discord.py cannot catch for us, since it only
validates that the *parameter* exists. This walks the real command tree and checks the other half.
"""

import importlib
from types import SimpleNamespace
from typing import Any

import pytest
from discord import app_commands
from discord.ext import commands as ext_commands

from squid.suggestions.application import SuggestionRegistry
from squid.suggestions.infrastructure.catalogue import build_registry

COMMAND_MODULES = [
    "squid.bot.admin",
    "squid.bot.help",
    "squid.bot.notifications",
    "squid.bot.permissions",
    "squid.bot.settings",
    "squid.bot.starboard.cog",
    "squid.bot.submission.edit",
    "squid.bot.submission.records",
    "squid.bot.submission.schematics",
    "squid.bot.submission.search",
    "squid.bot.submission.submit",
    "squid.bot.verify",
    "squid.bot.version_tracking",
]


def discord_registry() -> SuggestionRegistry:
    stub: Any = SimpleNamespace()
    return build_registry(
        repository=stub,
        search=stub,
        versions=stub,
        tags=stub,
        starboards=stub,
        permission_roles=stub,
        notifications=stub,
        accounts=stub,
    )


def autocompleted_parameters() -> list[tuple[str, str, app_commands.Parameter]]:
    """Walk every loaded command and collect the parameters carrying an autocomplete callback."""
    found: list[tuple[str, str, app_commands.Parameter]] = []

    def walk(command: object) -> None:
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                walk(child)
            return
        name = getattr(command, "qualified_name", "?")
        found.extend(
            (name, parameter.name, parameter)
            for parameter in getattr(command, "_params", {}).values()
            if parameter.autocomplete is not None
        )

    for module_name in COMMAND_MODULES:
        module = importlib.import_module(module_name)
        for attribute in list(vars(module).values()):
            if not isinstance(attribute, type):
                continue
            for member in vars(attribute).values():
                if isinstance(member, ext_commands.HybridCommand | ext_commands.HybridGroup):
                    if member.app_command is not None:
                        walk(member.app_command)
                elif isinstance(member, app_commands.Command | app_commands.Group):
                    walk(member)
    return found


PARAMETERS = autocompleted_parameters()


def test_the_command_tree_actually_uses_autocomplete() -> None:
    """Guards the walker itself: an empty list would make every check below vacuous."""
    assert PARAMETERS
    assert {command for command, _, _ in PARAMETERS} >= {"build browse", "build submit", "search", "tags"}


@pytest.mark.parametrize(
    ("command", "parameter", "source_id"),
    [
        (command, parameter, source)
        for command, parameter, handle in PARAMETERS
        if (source := getattr(handle.autocomplete, "__squid_source__", None)) is not None
    ],
)
def test_every_referenced_source_is_registered(command: str, parameter: str, source_id: str) -> None:
    assert source_id in discord_registry(), f"/{command} {parameter} names unregistered source {source_id!r}"

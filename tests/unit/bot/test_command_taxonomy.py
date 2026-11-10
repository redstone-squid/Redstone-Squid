"""Regression tests for the public Discord command taxonomy."""

from collections.abc import Iterable
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext.commands import Command

from squid.bot.admin import Admin
from squid.bot.diagnostics import Diagnostics
from squid.bot.give_redstoner import GiveRedstoner
from squid.bot.layout_showcase import LayoutShowcaseCog
from squid.bot.permissions import PermissionCog
from squid.bot.settings import SettingsCog
from squid.bot.starboard import StarboardCog
from squid.bot.submission.records import RecordCog
from squid.bot.submission.search import SearchCog
from squid.bot.verify import VerifyCog
from squid.bot.version_tracking import VersionTracker
from squid.bot.voting.vote import VoteCog

type AnyCommand = Command[Any, ..., Any]

UNGATED_COMMANDS = frozenset(
    {
        # Public: anyone may run these.
        "layout",
        "layout demo",
        "layout lobby",
        "layout shared",
    }
)
"""Explicit exceptions to the permission-node requirement for commands in PUBLIC_COGS."""

PUBLIC_COGS = (
    SearchCog,
    Admin,
    Diagnostics,
    RecordCog,
    VoteCog,
    VerifyCog,
    VersionTracker,
    SettingsCog,
    LayoutShowcaseCog,
    GiveRedstoner,
    StarboardCog,
    PermissionCog,
)

PICKER_VISIBILITY: dict[str, frozenset[str]] = {
    # Default picker visibility for top-level commands; guild admins can override it.
    # Runtime permission checks remain authoritative. Comparing the whole map keeps
    # newly restricted or newly public commands visible in review.
    "errors": frozenset({"manage_guild"}),
    "access": frozenset({"manage_guild"}),
    "records": frozenset({"manage_guild"}),
    "redstoner": frozenset({"manage_roles"}),
    "settings": frozenset({"manage_guild"}),
    "starboard": frozenset({"manage_guild"}),
}
"""Top-level commands hidden from pickers, and what a viewer needs to see them."""


def _default_permissions(command: AnyCommand) -> frozenset[str] | None:
    """The Discord permissions gating a command's visibility, by name."""
    application = getattr(command, "app_command", None) or command
    permissions = getattr(application, "default_permissions", None)
    if not isinstance(permissions, discord.Permissions):
        return None
    return frozenset(name for name, enabled in permissions if enabled)


def _commands_of(cog: type) -> list[AnyCommand]:
    """Read a cog's command tree without constructing it.

    `__new__` skips `__init__`, which wants a live bot; pyrefly cannot follow
    that through a heterogeneous list of cog classes, hence the cast.
    """
    return cast(Any, cog).__new__(cog).__cog_commands__


def _nodes(commands: Iterable[AnyCommand], qualified_name: str) -> set[str]:
    """The permission nodes a command declares, read off its check predicates."""
    command = next(command for command in commands if command.qualified_name == qualified_name)
    return {node for predicate in command.checks for node in getattr(predicate, "__squid_nodes__", ())}


def test_build_slash_group_includes_the_app_only_workspaces() -> None:
    build_group = next(
        command for command in cast(Any, SearchCog).__cog_app_commands__ if command.qualified_name == "build"
    )

    assert {command.qualified_name for command in build_group.walk_commands()} == {
        "build browse",
        "build submit",
        "build drafts",
    }


def test_guided_submit_puts_attachments_last() -> None:
    """Typed fields lead the option tab order, followed by attachments."""
    build_group = next(
        command for command in cast(Any, SearchCog).__cog_app_commands__ if command.qualified_name == "build"
    )
    submit = next(command for command in build_group.walk_commands() if command.qualified_name == "build submit")
    names = [parameter.name for parameter in cast(app_commands.Command[Any, ..., Any], submit).parameters]

    assert names[0] == "door_size"
    assert [name for name in names if name.endswith("_attachment")] == names[-4:]


def test_search_modes_have_user_friendly_labels() -> None:
    cog = cast(Any, SearchCog)
    search = next(command for command in cog.__cog_app_commands__ if command.qualified_name == "search")
    mode = next(
        parameter
        for parameter in cast(app_commands.Command[Any, ..., Any], search).parameters
        if parameter.name == "mode"
    )
    assert [(choice.name, choice.value) for choice in mode.choices] == [
        ("keyword", "keyword"),
        ("smart", "smart"),
    ]


def test_every_privileged_command_declares_a_node() -> None:
    """Commands without permission nodes require an explicit allowlist entry."""
    ungated: set[str] = set()
    for cog in PUBLIC_COGS:
        for command in _commands_of(cog):
            if command.hidden:
                continue
            for entry in (command, *getattr(command, "commands", ())):
                if not _nodes([entry], entry.qualified_name):
                    ungated.add(entry.qualified_name)

    assert ungated == UNGATED_COMMANDS


def test_staff_groups_are_hidden_from_non_staff_pickers() -> None:
    """Compare the complete map so a missing visibility hint cannot go unnoticed."""
    visibility = {
        command.qualified_name: names
        for cog in PUBLIC_COGS
        for command in (*_commands_of(cog), *cast(Any, cog).__cog_app_commands__)
        if command.parent is None and (names := _default_permissions(command)) is not None
    }

    assert visibility == PICKER_VISIBILITY


def test_subcommands_do_not_claim_a_visibility_they_would_not_get() -> None:
    """Discord reads `default_member_permissions` on top-level commands only.

    On a subcommand it is accepted and ignored, so one written there would read
    as a gate in the source while doing nothing at all.
    """
    mislabelled = {
        command.qualified_name
        for cog in PUBLIC_COGS
        for command in _commands_of(cog)
        if command.parent is not None and _default_permissions(command) is not None
    }

    assert mislabelled == set()


def test_polls_are_one_app_only_command() -> None:
    """Poll creation opens a modal and therefore requires an application-command interaction."""
    cog = VoteCog.__new__(VoteCog)

    assert [command.qualified_name for command in cog.__cog_app_commands__] == ["poll"]
    assert not [command.qualified_name for command in cog.__cog_commands__]


def test_settings_are_one_app_only_workspace() -> None:
    settings = cast(Any, SettingsCog)
    assert settings.__cog_commands__ == []
    assert [command.qualified_name for command in settings.__cog_app_commands__] == ["settings"]


def test_access_is_one_app_only_workspace() -> None:
    access = cast(Any, PermissionCog)
    assert access.__cog_commands__ == []
    assert [command.qualified_name for command in access.__cog_app_commands__] == ["access"]

"""Regression tests for the public Discord command taxonomy."""

from collections.abc import Iterable
from typing import Any, cast

from discord.ext.commands import Command, HybridCommand, HybridGroup

from squid.bot.admin import Admin
from squid.bot.give_redstoner import GiveRedstoner
from squid.bot.misc_commands import Miscellaneous
from squid.bot.settings import SettingsCog
from squid.bot.submission.records import RecordCog
from squid.bot.submission.search import SearchCog
from squid.bot.verify import VerifyCog
from squid.bot.version_tracking import VersionTracker
from squid.bot.voting.vote import VoteCog

type AnyCommand = Command[Any, ..., Any]

PUBLIC_COGS = (
    SearchCog,
    Admin,
    RecordCog,
    VoteCog,
    VerifyCog,
    VersionTracker,
    SettingsCog,
    Miscellaneous,
    GiveRedstoner,
)

EXPECTED_PREFIX_COMMAND_TREE: dict[str, tuple[str, ...]] = {
    "account": ("approve-claim", "claim", "claims", "link", "reject-claim", "unlink"),
    "admin": ("records-gaps", "records-lookup", "records-rebuild", "records-title-issues"),
    "archive": (),
    "build": (
        "approve",
        "debug",
        "edit",
        "queue",
        "recalc",
        "reject",
        "schematic",
        "schematic convert",
        "schematic download",
        "schematic info",
        "submit-full",
        "view",
    ),
    "info": ("docs", "form", "invite", "source"),
    "patterns": ("list", "search"),
    "redstoner": ("panel", "resync"),
    "restrictions": ("add-alias", "search"),
    "search": (),
    "settings": ("clear", "get", "list", "locale", "set"),
    "tag": ("apply", "approve", "archive", "pending", "propose", "reject"),
    "version": ("add", "list"),
    "vote": ("delete",),
}


def _public_command_names() -> set[str]:
    return {command.qualified_name for cog in PUBLIC_COGS for command in cog.__cog_commands__ if not command.hidden}  # type: ignore


def _command(commands: Iterable[AnyCommand], qualified_name: str) -> AnyCommand:
    return next(command for command in commands if command.qualified_name == qualified_name)


def _qualified_names(command_tree: dict[str, tuple[str, ...]]) -> set[str]:
    return set(command_tree) | {
        f"{group} {command}" for group, commands in command_tree.items() for command in commands
    }


def _assert_check_counts(commands: Iterable[AnyCommand], expected: dict[str, int]) -> None:
    assert {name: len(_command(commands, name).checks) for name in expected} == expected


def test_public_prefix_command_tree_matches_taxonomy() -> None:
    assert _public_command_names() == _qualified_names(EXPECTED_PREFIX_COMMAND_TREE)


def test_build_slash_group_includes_app_only_guided_submit() -> None:
    cog = SearchCog.__new__(SearchCog)
    build_group = cast(HybridGroup, _command(cog.__cog_commands__, "build"))
    expected_commands = {f"build {command}" for command in (*EXPECTED_PREFIX_COMMAND_TREE["build"], "submit")}

    assert {command.qualified_name for command in build_group.app_command.walk_commands()} == expected_commands


def test_search_modes_have_user_friendly_labels() -> None:
    cog = SearchCog.__new__(SearchCog)
    search = cast(HybridCommand, _command(cog.__cog_commands__, "search"))
    assert search.app_command is not None
    mode = next(parameter for parameter in search.app_command.parameters if parameter.name == "mode")
    assert [(choice.name, choice.value) for choice in mode.choices] == [
        ("keyword", "keyword"),
        ("smart", "smart"),
    ]


def test_staff_and_owner_checks_remain_on_sensitive_commands() -> None:
    search = SearchCog.__new__(SearchCog)
    _assert_check_counts(
        search.__cog_commands__,
        {
            "build approve": 2,
            "build debug": 1,
            "build reject": 2,
            "build edit": 2,
            "build recalc": 2,
        },
    )

    records = RecordCog.__new__(RecordCog)
    _assert_check_counts(
        records.__cog_commands__,
        {
            "admin": 1,
            "admin records-gaps": 1,
            "admin records-lookup": 1,
            "admin records-rebuild": 2,
            "admin records-title-issues": 3,
        },
    )

    verify = VerifyCog.__new__(VerifyCog)
    _assert_check_counts(
        verify.__cog_commands__,
        {
            "account claim": 0,
            "account claims": 2,
            "account approve-claim": 2,
            "account reject-claim": 2,
        },
    )

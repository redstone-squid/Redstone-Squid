"""Regression tests for the public Discord command taxonomy."""

from collections.abc import Iterable
from typing import Any, cast

from discord.ext.commands import Command, HybridCommand, HybridGroup

from squid.bot.admin import Admin
from squid.bot.give_redstoner import GiveRedstoner
from squid.bot.misc_commands import Miscellaneous
from squid.bot.settings import SettingsCog
from squid.bot.starboard import StarboardCog
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
    StarboardCog,
)

EXPECTED_PREFIX_COMMAND_TREE: dict[str, tuple[str, ...]] = {
    "account": ("approve-claim", "claim", "claims", "link", "reject-claim", "unlink"),
    "admin": (
        "global-admin",
        "global-admin add",
        "global-admin list",
        "global-admin remove",
        "records-gaps",
        "records-lookup",
        "records-rebuild",
        "records-title-issues",
    ),
    "archive": (),
    "build": (
        "approve",
        "debug",
        "detect-lattice",
        "edit",
        "measure-timing",
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
    "settings": (
        "clear",
        "get",
        "list",
        "locale",
        "set",
        "voting",
        "voting emojis",
        "voting reset",
        "voting show",
        "voting weight-remove",
        "voting weight-set",
    ),
    "starboard": (
        "create",
        "delete",
        "edit",
        "emoji",
        "emoji add",
        "emoji list",
        "emoji remove",
        "list",
        "recount",
        "show",
        "weight",
        "weight list",
        "weight remove",
        "weight set",
    ),
    "tag": ("apply", "approve", "archive", "pending", "propose", "reject"),
    "version": ("add", "list"),
    "vote": ("close", "delete", "poll", "refresh"),
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


def _check_names(commands: Iterable[AnyCommand], qualified_name: str) -> set[str]:
    return {predicate.__qualname__.partition(".<locals>")[0] for predicate in _command(commands, qualified_name).checks}


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


def test_administrator_and_owner_checks_remain_on_sensitive_commands() -> None:
    search = SearchCog.__new__(SearchCog)
    _assert_check_counts(
        search.__cog_commands__,
        {
            "build approve": 1,
            "build debug": 1,
            "build detect-lattice": 1,
            "build reject": 1,
            "build edit": 1,
            "build measure-timing": 1,
            "build recalc": 1,
        },
    )

    records = RecordCog.__new__(RecordCog)
    _assert_check_counts(
        records.__cog_commands__,
        {
            "admin": 1,
            "admin records-gaps": 1,
            "admin records-lookup": 1,
            "admin records-rebuild": 1,
            "admin records-title-issues": 1,
        },
    )

    verify = VerifyCog.__new__(VerifyCog)
    _assert_check_counts(
        verify.__cog_commands__,
        {
            "account claim": 0,
            "account claims": 1,
            "account approve-claim": 1,
            "account reject-claim": 1,
        },
    )


def test_sensitive_commands_use_the_intended_permission_tier() -> None:
    search = SearchCog.__new__(SearchCog)
    assert _check_names(search.__cog_commands__, "build approve") == {"check_is_global_admin"}
    assert _check_names(search.__cog_commands__, "build edit") == {"check_is_home_server_trusted_or_global_admin"}
    assert _check_names(search.__cog_commands__, "build measure-timing") == {"check_is_trusted_or_global_admin"}

    settings = SettingsCog.__new__(SettingsCog)
    assert _check_names(settings.__cog_commands__, "settings set") == {"check_is_server_admin"}

    starboard = StarboardCog.__new__(StarboardCog)
    assert _check_names(starboard.__cog_commands__, "starboard") == {"check_is_server_admin", "guild_only"}

    admin = Admin.__new__(Admin)
    assert _check_names(admin.__cog_commands__, "tag approve") == {"check_is_global_admin"}
    assert _check_names(admin.__cog_commands__, "archive") == {"check_is_server_admin"}

    records = RecordCog.__new__(RecordCog)
    assert _check_names(records.__cog_commands__, "admin global-admin") == {"is_owner"}
    assert _check_names(records.__cog_commands__, "admin records-rebuild") == {"is_owner"}

    redstoner = GiveRedstoner.__new__(GiveRedstoner)
    assert _check_names(redstoner.__cog_commands__, "redstoner panel") == {
        "check_is_home_server",
        "check_is_server_admin",
    }

"""Regression tests for the public Discord command taxonomy."""

from collections.abc import Iterable
from typing import Any, cast

from discord import app_commands
from discord.ext.commands import Command, HybridCommand, HybridGroup

from squid.bot.admin import Admin
from squid.bot.diagnostics import Diagnostics
from squid.bot.give_redstoner import GiveRedstoner
from squid.bot.misc_commands import Miscellaneous
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
        "account",
        "account claim",
        "account consent",
        "account identities",
        "account link",
        "account merge",
        "account merge-code",
        "account profile",
        "account profile-edit",
        # Refreshing your own Minecraft name is default-allow, so the command declares no
        # node. Its `user:` form checks `account.identity.refresh_any` inline instead, which
        # a decorator could not express without gating the self case too.
        "account refresh",
        "account unlink",
        "account visibility",
        "build",
        "build queue",
        "build schematic",
        "build schematic convert",
        "build schematic download",
        "build schematic info",
        "build schematic render",
        "build view",
        "info",
        "info docs",
        "info form",
        "info invite",
        "info source",
        "patterns",
        "patterns list",
        "patterns search",
        # Anyone in a guild may open a poll, so `poll create` needs no node. Closing and
        # refreshing one are authorized against the session rather than the caller: both
        # run `VoteSessionSnapshot.can_close`, which admits the poll's author as well as
        # holders of `vote.poll.close_any`. A node on the command could not express "or
        # you opened this one", and would lock authors out of their own polls.
        "poll",
        "poll close",
        "poll create",
        "poll refresh",
        "restrictions",
        "restrictions search",
        "search",
        "tag",
        "tag apply",
        "tag propose",
        "version",
        "version list",
        "vote",
        "vote poll",
        "vote delete",
    }
)
"""Commands that legitimately declare no permission node: the public ones.

Everything else in `PUBLIC_COGS` must declare one, so a privileged command
shipped without a gate fails CI instead of shipping open.
"""

PUBLIC_COGS = (
    SearchCog,
    Admin,
    Diagnostics,
    RecordCog,
    VoteCog,
    VerifyCog,
    VersionTracker,
    SettingsCog,
    Miscellaneous,
    GiveRedstoner,
    StarboardCog,
    PermissionCog,
)

EXPECTED_PREFIX_COMMAND_TREE: dict[str, tuple[str, ...]] = {
    # The command surface users see, restated so that changing it is a reviewable diff.
    #
    # Kept deliberately, despite the maintenance: a command is a public interface, and
    # both directions of drift are silent otherwise. A command dropped by a refactor
    # takes a documented entry point away with no failing test, and a command added
    # without a plan ships to every guild. Neither shows up in a behavioural test,
    # because the behaviour of a command nobody calls is nothing.
    "account": (
        "approve-claim",
        "claim",
        "claims",
        "consent",
        "identities",
        "link",
        "merge",
        "merge-code",
        "profile",
        "profile-edit",
        "refresh",
        "reject-claim",
        "unlink",
        "visibility",
    ),
    "admin": (
        "records-gaps",
        "records-lookup",
        "records-rebuild",
        "records-title-issues",
    ),
    "archive": (),
    # `error` is a hybrid group with a `show` fallback, so bare `error <reference>` works.
    "error": ("recent",),
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
        "schematic render",
        "view",
    ),
    "info": ("docs", "form", "invite", "source"),
    "patterns": ("list", "search"),
    "perm": (
        "audit",
        "deny",
        "explain",
        "forbid",
        "grant",
        "list",
        "nodes",
        "revoke",
        "test",
        "whoami",
    ),
    # Polls got their own top-level group in `509406c2`, because a poll is not a vote on
    # a build: it stands alone, has no submission behind it, and is closed by whoever
    # opened it. `vote poll` survives only as a deprecated alias for `poll create`.
    "poll": ("close", "create", "refresh"),
    "redstoner": ("panel", "resync"),
    "restrictions": ("add-alias", "search"),
    "role": (
        "add-role",
        "assign",
        "create",
        "delete",
        "exclude",
        "include",
        "list",
        "rank",
        "remove-pattern",
        "remove-role",
        "show",
        "unassign",
    ),
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
    "vote": ("delete", "poll"),
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


def _commands_of(cog: type) -> list[AnyCommand]:
    """Read a cog's command tree without constructing it.

    `__new__` skips `__init__`, which wants a live bot; pyrefly cannot follow
    that through a heterogeneous list of cog classes, hence the cast.
    """
    return cast(Any, cog).__new__(cog).__cog_commands__


def _nodes(commands: Iterable[AnyCommand], qualified_name: str) -> set[str]:
    """The permission nodes a command declares, read off its check predicates."""
    return {
        node
        for predicate in _command(commands, qualified_name).checks
        for node in getattr(predicate, "__squid_nodes__", ())
    }


def test_public_prefix_command_tree_matches_taxonomy() -> None:
    """Adding or removing a user-visible command must be a deliberate edit here."""
    assert _public_command_names() == _qualified_names(EXPECTED_PREFIX_COMMAND_TREE)


def test_build_slash_group_includes_app_only_guided_submit() -> None:
    cog = SearchCog.__new__(SearchCog)
    build_group = cast(HybridGroup, _command(cog.__cog_commands__, "build"))
    expected_commands = {f"build {command}" for command in (*EXPECTED_PREFIX_COMMAND_TREE["build"], "submit")}

    assert {command.qualified_name for command in build_group.app_command.walk_commands()} == expected_commands


def test_guided_submit_puts_attachments_last() -> None:
    """The tab order through the options is the form: typed fields first, attachments trailing.

    Attachments-first was the original dogfooding complaint against `/build submit`
    (docs/plans/command-redesign/01-build-submit.md), so the order is pinned.
    """
    cog = SearchCog.__new__(SearchCog)
    build_group = cast(HybridGroup, _command(cog.__cog_commands__, "build"))
    submit = next(
        command for command in build_group.app_command.walk_commands() if command.qualified_name == "build submit"
    )
    names = [parameter.name for parameter in cast(app_commands.Command[Any, ..., Any], submit).parameters]

    assert names[0] == "door_size"
    assert [name for name in names if name.endswith("_attachment")] == names[-4:]


def test_search_modes_have_user_friendly_labels() -> None:
    cog = SearchCog.__new__(SearchCog)
    search = cast(HybridCommand, _command(cog.__cog_commands__, "search"))
    assert search.app_command is not None
    mode = next(parameter for parameter in search.app_command.parameters if parameter.name == "mode")
    assert [(choice.name, choice.value) for choice in mode.choices] == [
        ("keyword", "keyword"),
        ("smart", "smart"),
    ]


def test_sensitive_commands_declare_the_intended_permission_nodes() -> None:
    """The node contract, read from the predicate rather than from its name.

    The old form counted checks and compared their qualified names, which could
    only tell that *a* tier was applied. A node set is the actual contract, so a
    command silently regated to the wrong capability fails here.
    """
    search = SearchCog.__new__(SearchCog)
    assert _nodes(search.__cog_commands__, "build approve") == {"build.submission.approve"}
    assert _nodes(search.__cog_commands__, "build reject") == {"build.submission.reject"}
    assert _nodes(search.__cog_commands__, "build debug") == {"build.submission.debug"}
    assert _nodes(search.__cog_commands__, "build edit") == {"build.submission.edit"}
    assert _nodes(search.__cog_commands__, "build recalc") == {"build.submission.recalc"}
    assert _nodes(search.__cog_commands__, "build measure-timing") == {"build.schematic.measure_timing"}
    assert _nodes(search.__cog_commands__, "build detect-lattice") == {"build.schematic.detect_lattice"}
    assert _nodes(search.__cog_commands__, "restrictions add-alias") == {"restriction.alias.create"}

    settings = SettingsCog.__new__(SettingsCog)
    assert _nodes(settings.__cog_commands__, "settings set") == {"settings.server.edit"}
    assert _nodes(settings.__cog_commands__, "settings list") == {"settings.server.view"}

    starboard = StarboardCog.__new__(StarboardCog)
    assert _nodes(starboard.__cog_commands__, "starboard create") == {"starboard.board.create"}
    assert _nodes(starboard.__cog_commands__, "starboard recount") == {"starboard.board.recount"}

    diagnostics = Diagnostics.__new__(Diagnostics)
    assert _nodes(diagnostics.__cog_commands__, "error") == {"diagnostics.error.read"}
    assert _nodes(diagnostics.__cog_commands__, "error recent") == {"diagnostics.error.read"}

    admin = Admin.__new__(Admin)
    assert _nodes(admin.__cog_commands__, "tag approve") == {"tag.proposal.approve"}
    assert _nodes(admin.__cog_commands__, "archive") == {"message.archive.create"}

    records = RecordCog.__new__(RecordCog)
    assert _nodes(records.__cog_commands__, "admin records-rebuild") == {"record.entry.rebuild"}
    assert _nodes(records.__cog_commands__, "admin records-lookup") == {"record.entry.inspect"}

    verify = VerifyCog.__new__(VerifyCog)
    assert _nodes(verify.__cog_commands__, "account claims") == {"account.claim.list"}
    assert _nodes(verify.__cog_commands__, "account claim") == set()

    redstoner = GiveRedstoner.__new__(GiveRedstoner)
    assert _nodes(redstoner.__cog_commands__, "redstoner panel") == {"redstoner.panel.manage"}


def test_group_gates_admit_anyone_holding_one_of_their_commands_nodes() -> None:
    """A group gate must not be narrower than the commands inside it.

    Every node is separately grantable, so gating a group on one node would make
    the others unreachable for anyone granted only those.
    """
    for cog, group, member in (
        (SettingsCog, "settings", "settings set"),
        (StarboardCog, "starboard", "starboard recount"),
        (RecordCog, "admin", "admin records-rebuild"),
    ):
        commands = _commands_of(cog)
        assert _nodes(commands, member) <= _nodes(commands, group)


def test_every_privileged_command_declares_a_node() -> None:
    """A privileged command shipped with no gate fails CI.

    The allowlist is the whole point: adding a command to it is a visible,
    reviewable decision, whereas forgetting a check is silent.
    """
    ungated: set[str] = set()
    for cog in PUBLIC_COGS:
        for command in _commands_of(cog):
            if command.hidden:
                continue
            for entry in (command, *getattr(command, "commands", ())):
                if not _nodes([entry], entry.qualified_name):
                    ungated.add(entry.qualified_name)

    assert ungated == UNGATED_COMMANDS

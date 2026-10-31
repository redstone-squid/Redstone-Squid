"""Regression tests for the public Discord command taxonomy."""

from collections.abc import Iterable
from typing import Any, cast

import discord
from discord import app_commands
from discord.ext.commands import Command, HybridCommand, HybridGroup

from squid.bot.admin import Admin
from squid.bot.diagnostics import Diagnostics
from squid.bot.give_redstoner import GiveRedstoner
from squid.bot.layout_showcase import LayoutShowcaseCog
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
        "account link",
        "account merge",
        "account merge-code",
        # Refreshing your own Minecraft name is default-allow, so the command declares no
        # node. Its `user:` form checks `account.identity.refresh_any` inline instead, which
        # a decorator could not express without gating the self case too.
        "account refresh",
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
        "layout",
        "layout demo",
        "layout shared",
        "search",
        "tag",
        "tag apply",
        "tag propose",
        "version",
        "version list",
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
    LayoutShowcaseCog,
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
    # Approving and rejecting a claim are buttons on `account claims`, not commands
    # (docs/plans/command-redesign/05-condensation.md). `account` is a hybrid group with a
    # `show` fallback, so bare `account` opens the panel that `identities`, `visibility`,
    # `unlink`, `profile` and `profile-edit` used to answer a piece at a time
    # (docs/plans/command-redesign/07-account.md).
    "account": (
        "claim",
        "claims",
        "consent",
        "link",
        "merge",
        "merge-code",
        "refresh",
    ),
    "archive": (),
    # `error` is a hybrid group with a `show` fallback, so bare `error <reference>` works.
    "error": ("clear", "recent"),
    # The schematic tools all sit under `build schematic`, which is what their
    # permission nodes always said (docs/plans/command-redesign/06-build.md).
    "build": (
        "approve",
        "debug",
        "queue",
        "reject",
        "schematic",
        "schematic convert",
        "schematic detect-lattice",
        "schematic download",
        "schematic info",
        "schematic measure-timing",
        "schematic render",
        "view",
    ),
    "info": ("docs", "form", "invite", "source"),
    "layout": ("demo", "shared"),
    # `whoami`, `test` and `explain` were three spellings of "what may this person do";
    # `can` is the one (docs/plans/command-redesign/05-condensation.md).
    "perm": (
        "audit",
        "can",
        "deny",
        "forbid",
        "grant",
        "list",
        "nodes",
        "revoke",
    ),
    # `admin` held nothing but record tooling, so every member repeated the group name it
    # actually wanted (docs/plans/command-redesign/05-condensation.md).
    "records": ("gaps", "lookup", "rebuild", "title-issues"),
    "redstoner": ("panel", "resync"),
    "restrictions": ("add-alias",),
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
    # `settings` is a hybrid group with a `show` fallback, so bare `settings` opens the panel
    # that `list`, `get`, `clear`, `voting show` and `voting emojis` used to answer one key at
    # a time (docs/plans/command-redesign/04-settings.md).
    "settings": (
        "locale",
        "set",
        "voting",
        "voting reset",
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
}


PICKER_VISIBILITY: dict[str, frozenset[str]] = {
    # Discord permissions that a viewer must hold for a top-level command to appear
    # in their picker at all (audit finding C1). Everything absent from this map is
    # visible to everyone, which is the default and has to stay a deliberate choice:
    # before this map existed, all ~108 commands showed up for every user and the
    # staff-only ones failed only after being invoked.
    #
    # These are visibility hints, never gates. `requires(...)` decides, and a guild
    # admin can override any of these per command in Server Settings; the bits below
    # are chosen to match the operation, so the override is rarely needed.
    "archive": frozenset({"manage_messages"}),
    "error": frozenset({"manage_guild"}),
    "perm": frozenset({"manage_guild"}),
    "records": frozenset({"manage_guild"}),
    "redstoner": frozenset({"manage_roles"}),
    "restrictions": frozenset({"manage_guild"}),
    "role": frozenset({"manage_guild"}),
    "settings": frozenset({"manage_guild"}),
    "starboard": frozenset({"manage_guild"}),
}
"""Top-level commands hidden from pickers, and what a viewer needs to see them."""


def _default_permissions(command: AnyCommand) -> frozenset[str] | None:
    """The Discord permissions gating a command's visibility, by name."""
    permissions = getattr(getattr(command, "app_command", None), "default_permissions", None)
    if not isinstance(permissions, discord.Permissions):
        return None
    return frozenset(name for name, enabled in permissions if enabled)


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


def test_build_slash_group_includes_the_app_only_workspaces() -> None:
    cog = SearchCog.__new__(SearchCog)
    build_group = cast(HybridGroup, _command(cog.__cog_commands__, "build"))
    # `submit` and `edit` are app-only: both open a workspace, which needs an interaction
    # (docs/plans/command-redesign/01-build-submit.md, 06-build.md).
    expected_commands = {f"build {command}" for command in (*EXPECTED_PREFIX_COMMAND_TREE["build"], "submit", "edit")}

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
    assert _nodes(search.__cog_commands__, "build schematic measure-timing") == {"build.schematic.measure_timing"}
    assert _nodes(search.__cog_commands__, "build schematic detect-lattice") == {"build.schematic.detect_lattice"}
    assert _nodes(search.__cog_commands__, "restrictions") == {"restriction.alias.create"}
    assert _nodes(search.__cog_commands__, "restrictions add-alias") == {"restriction.alias.create"}

    settings = SettingsCog.__new__(SettingsCog)
    assert _nodes(settings.__cog_commands__, "settings set") == {"settings.server.edit"}
    assert _nodes(settings.__cog_commands__, "settings locale") == {"settings.server.edit"}
    assert _nodes(settings.__cog_commands__, "settings voting") == {"settings.voting.edit"}

    starboard = StarboardCog.__new__(StarboardCog)
    assert _nodes(starboard.__cog_commands__, "starboard create") == {"starboard.board.create"}
    assert _nodes(starboard.__cog_commands__, "starboard recount") == {"starboard.board.recount"}

    diagnostics = Diagnostics.__new__(Diagnostics)
    assert _nodes(diagnostics.__cog_commands__, "error") == {"diagnostics.error.read"}
    assert _nodes(diagnostics.__cog_commands__, "error recent") == {"diagnostics.error.read"}
    assert _nodes(diagnostics.__cog_commands__, "error clear") == {"diagnostics.error.clear"}

    admin = Admin.__new__(Admin)
    assert _nodes(admin.__cog_commands__, "tag approve") == {"tag.proposal.approve"}
    assert _nodes(admin.__cog_commands__, "archive") == {"message.archive.create"}

    records = RecordCog.__new__(RecordCog)
    assert _nodes(records.__cog_commands__, "records rebuild") == {"record.entry.rebuild"}
    assert _nodes(records.__cog_commands__, "records lookup") == {"record.entry.inspect"}

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
        (SettingsCog, "settings", "settings voting"),
        (SettingsCog, "settings voting", "settings voting reset"),
        (StarboardCog, "starboard", "starboard recount"),
        (RecordCog, "records", "records rebuild"),
        (SearchCog, "restrictions", "restrictions add-alias"),
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


def test_staff_groups_are_hidden_from_non_staff_pickers() -> None:
    """Which commands the picker offers is part of the public surface.

    A staff group shipped without a visibility hint is invisible in review and
    very visible to users, so the whole map is pinned rather than each entry.
    """
    visibility = {
        command.qualified_name: names
        for cog in PUBLIC_COGS
        for command in _commands_of(cog)
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


def test_the_error_group_binds_a_reference_from_the_prefix_form() -> None:
    """`!error <reference>` works, contrary to what the redesign audit recorded.

    `HybridGroup.__init__` always sets `invoke_without_command`, and `Group.invoke` rewinds the
    argument view when the first word is not a subcommand, so the fallback's parameter binds on
    the prefix side too. Pinned because converting the group away from `hybrid_group` would take
    the prefix form away with nothing else failing.
    """
    cog = Diagnostics.__new__(Diagnostics)
    error = cast(HybridGroup, _command(cog.__cog_commands__, "error"))

    assert error.invoke_without_command is True
    assert error.fallback == "show"
    assert "reference" in error.clean_params


def test_polls_are_one_app_only_command() -> None:
    """A poll opens a modal, which a prefix invocation cannot do (audit C7).

    `poll` was a hybrid group whose members answered "use the slash command `/poll
    create`", so the prefix tree advertised three entry points and honoured none.
    Declaring it app-only is the audit's other option, and the group had nothing left to
    hold once `close` and `refresh` moved onto the poll card itself.
    """
    cog = VoteCog.__new__(VoteCog)

    assert [command.qualified_name for command in cog.__cog_app_commands__] == ["poll"]
    assert not [command.qualified_name for command in cog.__cog_commands__]


def test_the_settings_group_opens_the_panel_from_its_fallback() -> None:
    """`/settings` and `!settings` both have to reach the panel, not a help page.

    The panel is the phase 4 answer to setting a guild up one key per invocation, so it is the
    group's own callback rather than another subcommand somebody has to know about.
    """
    cog = SettingsCog.__new__(SettingsCog)
    group = cast(HybridGroup, _command(cog.__cog_commands__, "settings"))

    assert group.fallback == "show"
    assert group.invoke_without_command is True
    assert not group.clean_params

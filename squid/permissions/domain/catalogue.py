"""The permission node catalogue and the built-in role definitions.

Every separately grantable capability in the application is declared here exactly
once. Node names follow `<domain>.<resource>.<verb>`, borrowed from Google Cloud
IAM: a predictable shape is what makes `build.*.view` mean something, and an
inconsistent one would make wildcards guesswork.

Declaring a node and binding its constant are the same expression, so a duplicate
name fails at import time rather than at the call site that happens to lose.
"""

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from enum import StrEnum

from squid.core.i18n import _
from squid.permissions.domain.matching import MAX_SEGMENTS, Pattern
from squid.permissions.domain.models import (
    CatalogueError,
    Default,
    NodeScope,
    PermissionNode,
    Tag,
    UnknownPermissionNodeError,
)

MIN_SEGMENTS = 2
"""`<domain>.<verb>` is the shallowest a node may be. A single bare segment would
collide with a namespace and make `foo.**` ambiguous."""


class Catalogue:
    """An immutable, validated set of permission nodes."""

    def __init__(self, nodes: Iterable[PermissionNode]):
        self._nodes = {node.name: node for node in nodes}

    def __getitem__(self, name: str) -> PermissionNode:
        try:
            return self._nodes[name]
        except KeyError:
            raise UnknownPermissionNodeError.for_name(name) from None

    def __contains__(self, name: object) -> bool:
        return name in self._nodes

    def __iter__(self) -> Iterator[PermissionNode]:
        """Iterate nodes in name order, so renderings and snapshots are stable."""
        return iter(sorted(self._nodes.values(), key=lambda node: node.name))

    def __len__(self) -> int:
        return len(self._nodes)

    @property
    def names(self) -> tuple[str, ...]:
        """Every node name, sorted."""
        return tuple(sorted(self._nodes))

    def by_tag(self, tag: Tag) -> tuple[PermissionNode, ...]:
        """Every node carrying `tag`, in name order."""
        return tuple(node for node in self if tag in node.tags)

    def expand(self, pattern: Pattern | str) -> frozenset[str]:
        """The names of every node `pattern` currently selects.

        Expansion is a read-time convenience for rendering and validation. It is
        never stored: a grant keeps the pattern, so nodes added later fall under
        it automatically.
        """
        parsed = Pattern.parse(pattern) if isinstance(pattern, str) else pattern
        return frozenset(node.name for node in self if parsed.matches(node))

    def scopes_reached(self, pattern: Pattern | str) -> frozenset[NodeScope]:
        """The distinct scopes of the nodes `pattern` selects.

        Delegation validation uses this: a guild administrator may only issue a
        pattern that reaches `NodeScope.GUILD` nodes and nothing else.
        """
        parsed = Pattern.parse(pattern) if isinstance(pattern, str) else pattern
        return frozenset(node.scope for node in self if parsed.matches(node))


class CatalogueBuilder:
    """Accumulates node declarations and validates them as a whole."""

    def __init__(self) -> None:
        self._nodes: dict[str, PermissionNode] = {}

    def node(
        self,
        name: str,
        scope: NodeScope,
        description: str,
        *,
        default: Default = Default.DENY,
        tags: Sequence[Tag] = (),
    ) -> PermissionNode:
        """Declare a node and return it, so the constant and the registration agree."""
        if name in self._nodes:
            msg = f"Duplicate permission node: {name!r}."
            raise CatalogueError(msg)
        parsed = Pattern.parse(name)
        if parsed.is_tag or any(segment in ("*", "**") for segment in parsed.segments):
            msg = f"Permission node {name!r} must be a concrete name, not a pattern."
            raise CatalogueError(msg)
        if not MIN_SEGMENTS <= len(parsed.segments) <= MAX_SEGMENTS:
            msg = f"Permission node {name!r} must have between {MIN_SEGMENTS} and {MAX_SEGMENTS} segments."
            raise CatalogueError(msg)
        created = PermissionNode(
            name=name,
            scope=scope,
            description=description,
            default=default,
            tags=frozenset(tags),
        )
        self._nodes[name] = created
        return created

    def build(self) -> Catalogue:
        """Freeze the catalogue, rejecting structurally invalid node sets."""
        # A node that is also an interior namespace would make `foo.bar.**`
        # ambiguous: it could mean "the subtree" or "the subtree and its root".
        for name in self._nodes:
            prefix = f"{name}."
            shadowed = sorted(other for other in self._nodes if other.startswith(prefix))
            if shadowed:
                msg = f"Permission node {name!r} is also a namespace for {shadowed}."
                raise CatalogueError(msg)
        return Catalogue(self._nodes.values())


_b = CatalogueBuilder()

# --------------------------------------------------------------------------- #
# Global nodes.
#
# These reach the shared cross-guild build database or bot-wide state, so they
# are never delegable by a guild administrator and a guild-scoped rule can never
# satisfy them.
# --------------------------------------------------------------------------- #

BUILD_SUBMISSION_READ = _b.node(
    "build.submission.read",
    NodeScope.GLOBAL,
    _("View published builds."),
    default=Default.ALLOW,
    tags=(Tag.READONLY,),
)
BUILD_SUBMISSION_CREATE = _b.node(
    "build.submission.create",
    NodeScope.GLOBAL,
    _("Submit a build for review."),
    default=Default.ALLOW,
)
BUILD_SUBMISSION_EDIT = _b.node(
    "build.submission.edit",
    NodeScope.GLOBAL,
    _("Edit builds you did not submit."),
)
BUILD_SUBMISSION_APPROVE = _b.node(
    "build.submission.approve",
    NodeScope.GLOBAL,
    _("Approve pending build submissions."),
    tags=(Tag.MODERATION,),
)
BUILD_SUBMISSION_REJECT = _b.node(
    "build.submission.reject",
    NodeScope.GLOBAL,
    _("Reject pending build submissions."),
    tags=(Tag.MODERATION,),
)
BUILD_SUBMISSION_VIEW_PENDING = _b.node(
    "build.submission.view_pending",
    NodeScope.GLOBAL,
    _("View builds that have not been reviewed yet."),
    tags=(Tag.MODERATION, Tag.READONLY),
)
BUILD_SUBMISSION_RECALC = _b.node(
    "build.submission.recalc",
    NodeScope.GLOBAL,
    _("Recompute a build's derived attributes."),
)
BUILD_SUBMISSION_DEBUG = _b.node(
    "build.submission.debug",
    NodeScope.GLOBAL,
    _("Inspect a build's raw stored representation."),
    tags=(Tag.DIAGNOSTIC,),
)
BUILD_SCHEMATIC_MEASURE_TIMING = _b.node(
    "build.schematic.measure_timing",
    NodeScope.GLOBAL,
    _("Measure a schematic's timing."),
    tags=(Tag.DIAGNOSTIC,),
)
BUILD_SCHEMATIC_DETECT_LATTICE = _b.node(
    "build.schematic.detect_lattice",
    NodeScope.GLOBAL,
    _("Detect a schematic's lattice structure."),
    tags=(Tag.DIAGNOSTIC,),
)

RECORD_ENTRY_INSPECT = _b.node(
    "record.entry.inspect",
    NodeScope.GLOBAL,
    _("Inspect record gaps, lookups and title issues."),
    tags=(Tag.MODERATION, Tag.READONLY),
)
RECORD_ENTRY_REBUILD = _b.node(
    "record.entry.rebuild",
    NodeScope.GLOBAL,
    _("Rebuild the record table from scratch."),
    tags=(Tag.DESTRUCTIVE,),
)

TAG_PROPOSAL_LIST = _b.node(
    "tag.proposal.list",
    NodeScope.GLOBAL,
    _("List pending tag proposals."),
    tags=(Tag.MODERATION, Tag.READONLY),
)
TAG_PROPOSAL_APPROVE = _b.node(
    "tag.proposal.approve",
    NodeScope.GLOBAL,
    _("Approve a tag proposal."),
    tags=(Tag.MODERATION,),
)
TAG_PROPOSAL_REJECT = _b.node(
    "tag.proposal.reject",
    NodeScope.GLOBAL,
    _("Reject a tag proposal."),
    tags=(Tag.MODERATION,),
)
TAG_PROPOSAL_ARCHIVE = _b.node(
    "tag.proposal.archive",
    NodeScope.GLOBAL,
    _("Archive a tag."),
    tags=(Tag.MODERATION,),
)

RESTRICTION_ALIAS_CREATE = _b.node(
    "restriction.alias.create",
    NodeScope.GLOBAL,
    _("Add an alias for a restriction."),
    tags=(Tag.MODERATION,),
)

VERSION_ENTRY_CREATE = _b.node(
    "version.entry.create",
    NodeScope.GLOBAL,
    _("Register a new Minecraft version."),
)

ACCOUNT_CLAIM_LIST = _b.node(
    "account.claim.list",
    NodeScope.GLOBAL,
    _("List pending creator alias claims."),
    tags=(Tag.MODERATION, Tag.READONLY),
)
ACCOUNT_CLAIM_APPROVE = _b.node(
    "account.claim.approve",
    NodeScope.GLOBAL,
    _("Approve a creator alias claim."),
    tags=(Tag.MODERATION,),
)
ACCOUNT_CLAIM_REJECT = _b.node(
    "account.claim.reject",
    NodeScope.GLOBAL,
    _("Reject a creator alias claim."),
    tags=(Tag.MODERATION,),
)
ACCOUNT_VERIFY_RELAY = _b.node(
    "account.verify.relay",
    NodeScope.GLOBAL,
    _("Relay account verification on another user's behalf."),
)
ACCOUNT_SELF_READ = _b.node(
    "account.self.read",
    NodeScope.GLOBAL,
    _("Read your own account and notifications."),
    default=Default.ALLOW,
    tags=(Tag.READONLY,),
)
ACCOUNT_IDENTITY_REFRESH = _b.node(
    "account.identity.refresh",
    NodeScope.GLOBAL,
    _("Re-read your linked Minecraft name after a rename."),
    default=Default.ALLOW,
)
ACCOUNT_IDENTITY_REFRESH_ANY = _b.node(
    "account.identity.refresh_any",
    NodeScope.GLOBAL,
    _("Re-read another user's linked Minecraft name."),
    tags=(Tag.MODERATION,),
)

PERM_GRANT_GLOBAL = _b.node(
    "perm.grant.global",
    NodeScope.GLOBAL,
    _("Grant or revoke permissions anywhere, including global ones."),
)
ROLE_DEFINITION_MANAGE = _b.node(
    "role.definition.manage",
    NodeScope.GLOBAL,
    _("Create and edit global permission roles."),
)

DIAGNOSTICS_ERROR_READ = _b.node(
    "diagnostics.error.read",
    NodeScope.GLOBAL,
    _("Read a stored error report by the reference its user was shown."),
    tags=(Tag.DIAGNOSTIC, Tag.READONLY),
)

BOT_TREE_SYNC = _b.node(
    "bot.tree.sync",
    NodeScope.GLOBAL,
    _("Synchronise the application command tree with Discord."),
)
BOT_RUNTIME_DEBUG = _b.node(
    "bot.runtime.debug",
    NodeScope.GLOBAL,
    _("Run raw database and error-handling diagnostics against the live bot."),
    tags=(Tag.DESTRUCTIVE, Tag.DIAGNOSTIC),
)

# --------------------------------------------------------------------------- #
# Guild nodes.
#
# These affect a single Discord server, so a guild administrator may delegate
# them within their own guild.
# --------------------------------------------------------------------------- #

SETTINGS_SERVER_VIEW = _b.node(
    "settings.server.view",
    NodeScope.GUILD,
    _("View this server's bot settings."),
    tags=(Tag.READONLY,),
)
SETTINGS_SERVER_EDIT = _b.node(
    "settings.server.edit",
    NodeScope.GUILD,
    _("Change this server's bot settings."),
)
SETTINGS_VOTING_EDIT = _b.node(
    "settings.voting.edit",
    NodeScope.GUILD,
    _("Change this server's voting emojis and role weights."),
)

STARBOARD_BOARD_VIEW = _b.node(
    "starboard.board.view",
    NodeScope.GUILD,
    _("View this server's starboards."),
    tags=(Tag.READONLY,),
)
STARBOARD_BOARD_CREATE = _b.node(
    "starboard.board.create",
    NodeScope.GUILD,
    _("Create a starboard."),
)
STARBOARD_BOARD_EDIT = _b.node(
    "starboard.board.edit",
    NodeScope.GUILD,
    _("Change a starboard's configuration."),
)
STARBOARD_BOARD_DELETE = _b.node(
    "starboard.board.delete",
    NodeScope.GUILD,
    _("Delete a starboard and its recorded stars."),
    # Not @destructive despite deleting rows: that tag is subtracted by both
    # built-in admin roles, and deleting a starboard you created is routine guild
    # administration. @destructive is reserved for irreversible damage to state
    # shared across guilds.
)
STARBOARD_BOARD_RECOUNT = _b.node(
    "starboard.board.recount",
    NodeScope.GUILD,
    _("Recount a starboard's reactions."),
)
STARBOARD_EMOJI_EDIT = _b.node(
    "starboard.emoji.edit",
    NodeScope.GUILD,
    _("Change which emojis count towards a starboard."),
)
STARBOARD_WEIGHT_EDIT = _b.node(
    "starboard.weight.edit",
    NodeScope.GUILD,
    _("Change a starboard's per-role vote weights."),
)

MESSAGE_ARCHIVE_CREATE = _b.node(
    "message.archive.create",
    NodeScope.GUILD,
    _("Archive a channel's messages."),
)

REDSTONER_PANEL_MANAGE = _b.node(
    "redstoner.panel.manage",
    NodeScope.GUILD,
    _("Post and manage the Redstoner self-assign panel."),
)
REDSTONER_ROLE_RESYNC = _b.node(
    "redstoner.role.resync",
    NodeScope.GUILD,
    _("Resynchronise Redstoner role membership."),
)

VOTE_POLL_CAST = _b.node(
    "vote.poll.cast",
    NodeScope.GUILD,
    _("Vote in polls."),
    default=Default.ALLOW,
)
VOTE_POLL_CREATE = _b.node(
    "vote.poll.create",
    NodeScope.GUILD,
    _("Create a poll."),
)
VOTE_POLL_CLOSE_ANY = _b.node(
    "vote.poll.close_any",
    NodeScope.GUILD,
    _("Close or refresh a poll you did not create."),
)
VOTE_LOG_DELETE_CAST = _b.node(
    "vote.log_delete.cast",
    NodeScope.GUILD,
    _("Vote in build log deletion votes."),
)
VOTE_WEIGHT_STAFF = _b.node(
    "vote.weight.staff",
    NodeScope.GUILD,
    _("Have your votes counted at the staff weight."),
)

PERM_NODE_VIEW = _b.node(
    "perm.node.view",
    NodeScope.GUILD,
    _("Browse the permission catalogue and your own permissions."),
    default=Default.ALLOW,
    tags=(Tag.READONLY,),
)
PERM_SUBJECT_INSPECT = _b.node(
    "perm.subject.inspect",
    NodeScope.GUILD,
    _("Inspect and explain another user's permissions."),
    tags=(Tag.READONLY,),
)
PERM_AUDIT_VIEW = _b.node(
    "perm.audit.view",
    NodeScope.GUILD,
    _("Read the permission audit log."),
    tags=(Tag.READONLY,),
)
PERM_GRANT_GUILD = _b.node(
    "perm.grant.guild",
    NodeScope.GUILD,
    _("Grant or revoke this server's permissions."),
)
ROLE_DEFINITION_MANAGE_GUILD = _b.node(
    "role.definition.manage_guild",
    NodeScope.GUILD,
    _("Create and edit this server's permission roles."),
)

CATALOGUE = _b.build()
"""Every permission node the application defines."""


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    """A built-in role's identity and pattern lists.

    Built-in pattern lists live in code rather than in the database on purpose. A
    migration that seeded them would freeze the catalogue as it looked on the day
    it ran, so every node added afterwards would silently fall outside
    `global-admin`. Database rows for a built-in are additive overrides only.
    """

    key: str
    name: str
    description: str
    rank: int
    includes: tuple[str, ...]
    excludes: tuple[str, ...] = ()
    protected: bool = True


OWNER = RoleDefinition(
    key="owner",
    name="Owner",
    description="The bot owner. Holds everything, unconditionally.",
    rank=1000,
    includes=("**",),
)
GLOBAL_ADMIN = RoleDefinition(
    key="global-admin",
    name="Global administrator",
    description="Application-wide moderation, short of destructive and permission-granting powers.",
    rank=800,
    includes=(
        "build.**",
        "record.**",
        "tag.**",
        "restriction.**",
        "version.**",
        "account.**",
        "settings.**",
        "starboard.**",
        "message.**",
        "redstoner.**",
        "vote.**",
        # A new top-level namespace is not reached by any include above, so it has to be named
        # here or global administrators silently do not hold it.
        "diagnostics.**",
        "perm.subject.inspect",
        "perm.audit.view",
        "perm.grant.guild",
        "role.definition.manage_guild",
    ),
    # Expressed as subtraction rather than a hand-enumerated include list so that
    # a `@destructive` node added later is excluded without editing this role.
    excludes=("@destructive", "bot.**", "perm.grant.global", "role.definition.manage"),
)
GUILD_ADMIN = RoleDefinition(
    key="guild-admin",
    name="Server administrator",
    description="What Discord's Manage Server permission implies, as permission nodes.",
    rank=500,
    includes=(
        "settings.**",
        "starboard.**",
        "message.**",
        "redstoner.**",
        "vote.**",
        "perm.grant.guild",
        "perm.subject.inspect",
        "role.definition.manage_guild",
    ),
    excludes=("@destructive",),
)
TRUSTED = RoleDefinition(
    key="trusted",
    name="Trusted",
    description="The legacy Trusted tier: schematic diagnostics and weighted votes.",
    rank=200,
    includes=(
        "build.schematic.measure_timing",
        "build.schematic.detect_lattice",
        "vote.log_delete.cast",
        "vote.weight.staff",
    ),
)


class BuiltinRoleKeys(StrEnum):
    """`builtin_key` values, so nothing matches these by bare string."""

    OWNER = "owner"
    GLOBAL_ADMIN = "global-admin"
    GUILD_ADMIN = "guild-admin"
    TRUSTED = "trusted"


BUILTIN_ROLES: tuple[RoleDefinition, ...] = (OWNER, GLOBAL_ADMIN, GUILD_ADMIN, TRUSTED)
"""Built-in roles, highest rank first."""

BUILTIN_ROLES_BY_KEY: dict[str, RoleDefinition] = {role.key: role for role in BUILTIN_ROLES}

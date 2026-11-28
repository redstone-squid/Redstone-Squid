"""Bot authorization domain values."""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import ClassVar, Self, override

from squid.core.errors import ConfigurationError, InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid_i18n import Text


@dataclass(frozen=True, slots=True, kw_only=True)
class RolePermissionSubject:
    """Discord roles whose grants a moderator is inspecting within one guild."""

    guild_id: int
    discord_role_ids: frozenset[int] = frozenset()

    @property
    def is_bot_owner(self) -> bool:
        return False


class NodeScope(StrEnum):
    """Where a permission node's authority applies.

    `GLOBAL` nodes touch cross-guild state, such as the shared build database or
    bot-wide configuration. Only unscoped rules can affect them. `GUILD` nodes
    affect a single Discord server and may be delegated by its administrators.
    """

    GLOBAL = "global"
    GUILD = "guild"


class Default(StrEnum):
    """What a node resolves to when no rule matches it."""

    ALLOW = "allow"
    DENY = "deny"


class Tag(StrEnum):
    """A category that selects related nodes across namespaces.

    Roles can exclude a tag such as `@destructive` so newly declared nodes with
    that tag receive the same exclusion.
    """

    DESTRUCTIVE = "destructive"
    MODERATION = "moderation"
    DIAGNOSTIC = "diagnostic"
    READONLY = "readonly"


class Effect(IntEnum):
    """The verdict a matching rule requests.

    A more specific allow can override a broad deny. Forbid overrides other rules
    regardless of specificity, but the bot owner bypasses all rules.
    """

    ALLOW = 1
    DENY = -1
    FORBID = -2


@dataclass(frozen=True, slots=True)
class PermissionNode:
    """One separately grantable capability.

    Nodes are the leaves of the permission tree; patterns (`build.**`,
    `@destructive`) select them but are never themselves nodes.
    """

    name: str
    scope: NodeScope
    description: Text = field(hash=False)
    default: Default = Default.DENY
    tags: frozenset[Tag] = field(default_factory=frozenset)

    @override
    def __str__(self) -> str:
        return self.name

    @property
    def segments(self) -> tuple[str, ...]:
        """The dot-separated parts of the node name."""
        return tuple(self.name.split("."))


class InvalidPatternError(ValidationError):
    """A permission pattern is not well-formed."""

    default_message = tr(t"permissions-not-valid-permission = That is not a valid permission pattern.")
    default_title = tr(t"permissions-invalid-permission-pattern = Invalid permission pattern")


class UnknownPermissionNodeError(InvalidStateError):
    """A node name was resolved that the catalogue does not define.

    Raised rather than denied on purpose: a missing node is a programming error,
    and silently denying it would hide the bug behind a plausible-looking refusal.
    """

    @classmethod
    def for_name(cls, name: str) -> Self:
        return cls(f"Unknown permission node: {name!r}")


class CatalogueError(ConfigurationError):
    """The permission catalogue is inconsistent and cannot be built."""


class PatternMode(IntEnum):
    """How a stored pattern contributes to a role."""

    INCLUDE = 1
    EXCLUDE = -1


class AuditAction(StrEnum):
    """Permission mutations recorded transactionally with their effects."""

    GRANT = "grant"
    REVOKE = "revoke"
    ROLE_CREATE = "role_create"
    ROLE_DELETE = "role_delete"
    ROLE_WITHDRAW = "role_withdraw"
    ROLE_RANK = "role_rank"
    ROLE_INCLUDE = "role_include"
    ROLE_EXCLUDE = "role_exclude"
    ROLE_PATTERN_REMOVE = "role_pattern_remove"
    ROLE_COMPOSE = "role_compose"
    ROLE_DECOMPOSE = "role_decompose"
    ROLE_ASSIGN = "role_assign"
    ROLE_UNASSIGN = "role_unassign"


class AuditSubjectKind(StrEnum):
    ACCOUNT = "account"
    DISCORD_ROLE = "discord_role"


@dataclass(frozen=True, slots=True)
class AccountGrantSubject:
    """A permission membership attached to one internal account."""

    account_id: int
    kind: ClassVar[AuditSubjectKind] = AuditSubjectKind.ACCOUNT

    @property
    def discord_role_id(self) -> None:
        return None

    @property
    def guild_id(self) -> None:
        return None


@dataclass(frozen=True, slots=True)
class ExternalRoleGrantSubject:
    """A Discord role principal, scoped to the server that owns it."""

    guild_id: int
    discord_role_id: int
    kind: ClassVar[AuditSubjectKind] = AuditSubjectKind.DISCORD_ROLE

    @property
    def account_id(self) -> None:
        return None


type GrantSubject = AccountGrantSubject | ExternalRoleGrantSubject


class RoleListSort(StrEnum):
    ID = "id"
    HIGHEST_RANK = "highest_rank"


class GrantListSort(StrEnum):
    ID = "id"
    OLDEST = "oldest"


class AssignmentListSort(StrEnum):
    ID = "id"
    OLDEST = "oldest"


@dataclass(frozen=True, slots=True)
class GuildListFilters:
    guild_id: int | None = None


ALL_GUILDS = GuildListFilters()

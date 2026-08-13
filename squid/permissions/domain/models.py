"""Bot authorization domain values."""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Self

from whenever import Instant

from squid.core.errors import ConfigurationError, InvalidStateError, ValidationError
from squid.core.i18n import _


@dataclass(frozen=True, slots=True)
class GlobalAdministrator:
    """An account granted application-wide administrative access."""

    account_id: int
    granted_by_account_id: int
    granted_at: Instant


class NodeScope(StrEnum):
    """Where a permission node's authority applies.

    `GLOBAL` nodes touch cross-guild state (the shared build database, bot-wide
    configuration) and may only ever be granted globally. `GUILD` nodes affect a
    single Discord server and may be delegated by that server's administrators.
    """

    GLOBAL = "global"
    GUILD = "guild"


class Default(StrEnum):
    """What a node resolves to when no rule matches it."""

    ALLOW = "allow"
    DENY = "deny"


class Tag(StrEnum):
    """A semantic classification used to select nodes across the tree.

    Tags exist so a role can be defined as "this namespace, minus the dangerous
    parts" and stay correct as the catalogue grows: a leaf added later inherits
    the exclusion from its tag rather than needing every role to be edited.
    """

    DESTRUCTIVE = "destructive"
    MODERATION = "moderation"
    DIAGNOSTIC = "diagnostic"
    READONLY = "readonly"


class Effect(IntEnum):
    """The verdict a rule asserts for the nodes it matches.

    `DENY` loses to a more specific `ALLOW`, which is what makes "grant the
    namespace except this one leaf" work. `FORBID` short-circuits the whole
    resolution instead, and exists so the emergency stop does not have to win a
    specificity argument.
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
    description: str
    default: Default = Default.DENY
    tags: frozenset[Tag] = field(default_factory=frozenset)

    def __str__(self) -> str:
        return self.name

    @property
    def segments(self) -> tuple[str, ...]:
        """The dot-separated parts of the node name."""
        return tuple(self.name.split("."))


class InvalidPatternError(ValidationError):
    """A permission pattern is not well-formed."""

    default_message = _("That is not a valid permission pattern.")
    default_title = _("Invalid permission pattern")


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

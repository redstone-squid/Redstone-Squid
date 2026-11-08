"""Bot authorization domain values."""

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Self, override

from squid.core.errors import ConfigurationError, InvalidStateError, ValidationError
from squid.core.i18n import tr
from squid_ui.text import Message


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
    description: Message = field(hash=False)
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

    default_message = tr("That is not a valid permission pattern.")
    default_title = tr("Invalid permission pattern")


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

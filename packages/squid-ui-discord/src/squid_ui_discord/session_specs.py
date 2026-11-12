"""Session identity and admission policy, independent of rendering and delivery.

Session-key scope decides which openings compete. Application Scope owns presentation
lifetimes. ResponseSpec supplies access and rendering policy.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import discord

from squid_ui_discord.delivery import Replyable
from squid_ui_discord.sessions import (
    DEFAULT_ADMISSION,
    AdmissionSpec,
    GlobalScope,
    GuildScope,
    SessionKey,
    SessionScope,
    UserGuildScope,
    UserScope,
)


@dataclass(frozen=True, slots=True)
class OpenContext:
    """Discord identity from which screen policy is derived."""

    user_id: int
    guild_id: int | None = None

    @classmethod
    def of(cls, source: discord.Interaction[Any] | Replyable | discord.Message) -> OpenContext:
        """Extract opening identity from an interaction, command context, or message."""
        user = getattr(source, "user", None)
        if user is None:
            user = getattr(source, "author", None)
        if user is None:
            message = "open context source names neither a user nor an author"
            raise TypeError(message)
        guild_id = getattr(source, "guild_id", None)
        if guild_id is None:
            guild = getattr(source, "guild", None)
            guild_id = None if guild is None else guild.id
        return cls(user.id, guild_id)

    def user(self) -> UserScope:
        """This context's user, as a keyable scope."""
        return UserScope(self.user_id)

    def guild(self) -> GuildScope:
        """This context's guild, as a keyable scope. Raises in a DM."""
        return GuildScope(self._require_guild("guild"))

    def user_guild(self) -> UserGuildScope:
        """This context's user within its guild, as a keyable scope. Raises in a DM."""
        return UserGuildScope(self.user_id, self._require_guild("user_guild"))

    def global_(self) -> GlobalScope:
        """The process-global scope, which every open context reaches."""
        return GlobalScope()

    def _require_guild(self, kind: str) -> int:
        if self.guild_id is None:
            message = f"{kind} scopes require an open context with a guild"
            raise TypeError(message)
        return self.guild_id


class ScopeKind(StrEnum):
    """Session-key scope derivable from an :class:`OpenContext`."""

    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"

    def resolve(self, open_context: OpenContext) -> SessionScope:
        """Build the chosen scope; guild-based scopes raise when the opener has no guild.

        When the kind is known statically, OpenContext.user_guild and its peers return
        the specific scope type. These hashable values can also key an application-owned
        model dictionary, using a session's key.scope without a separate conversion.
        """
        match self:
            case ScopeKind.USER:
                return open_context.user()
            case ScopeKind.GUILD:
                return open_context.guild()
            case ScopeKind.USER_GUILD:
                return open_context.user_guild()
            case ScopeKind.GLOBAL:
                return open_context.global_()


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Identity and admission rules shared by openings of one logical screen."""

    name: str | None = None
    """Stable session identity; a Screen supplies its qualified class name when omitted."""

    scope: ScopeKind = ScopeKind.USER
    admission: AdmissionSpec = DEFAULT_ADMISSION
    capacity: int | None = None
    """The most members one opening of this screen admits; `None` is unbounded.

    Separate from `admission`, which governs how many sessions may occupy the key rather than
    how many users may join one of them.
    """
    quota: int | None = None
    """The most sessions in this screen's domain one user may be a member of at once.

    The dual of `capacity`: that caps users per session, this caps sessions per user. It
    binds at opening as well as at joining, because the opener joins by opening.
    """
    domain: str | None = None
    """The membership family `quota` counts within; the screen's `name` unless overridden.

    Set it when several screens share one family -- a lobby and the match it becomes are one
    "game" for the purpose of "one game at a time".
    """

    def key(self, open_context: OpenContext) -> SessionKey:
        """Derive this screen's session key from an opener."""
        if not isinstance(self.name, str) or not self.name:
            message = "SessionSpec.key requires a resolved non-empty name"
            raise ValueError(message)
        return SessionKey(self.name, self.scope.resolve(open_context))

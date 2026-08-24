"""Reusable per-open policy for Discord screens."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Unpack, cast

import discord

from squid_layouts.discord.access import AccessPolicy, Owner
from squid_layouts.discord.defaults import MountOptions
from squid_layouts.discord.delivery import Destination, respond_to
from squid_layouts.discord.mount import Mount
from squid_layouts.discord.sessions import (
    DEFAULT_SESSION_POLICY,
    GlobalScope,
    GuildScope,
    OpenResult,
    SessionKey,
    SessionPolicy,
    SessionRegistry,
    SessionScope,
    UserGuildScope,
    UserScope,
)
from squid_layouts.runtime.component import Component


@dataclass(frozen=True, slots=True)
class Opener:
    """Discord identity from which screen policy is derived."""

    user_id: int
    guild_id: int | None = None

    @classmethod
    def of(cls, interaction: discord.Interaction) -> Opener:
        """Build an opener from a Discord interaction."""
        return cls(interaction.user.id, interaction.guild_id)

    def user(self) -> UserScope:
        """This opener's user, as a keyable scope."""
        return UserScope(self.user_id)

    def guild(self) -> GuildScope:
        """This opener's guild, as a keyable scope. Raises in a DM."""
        return GuildScope(self._require_guild("guild"))

    def user_guild(self) -> UserGuildScope:
        """This opener's user within its guild, as a keyable scope. Raises in a DM."""
        return UserGuildScope(self.user_id, self._require_guild("user_guild"))

    def global_(self) -> GlobalScope:
        """The process-global scope, which every opener reaches."""
        return GlobalScope()

    def _require_guild(self, kind: str) -> int:
        if self.guild_id is None:
            message = f"{kind} scopes require an opener with a guild"
            raise TypeError(message)
        return self.guild_id


class Scope(StrEnum):
    """Session-key scope derivable from an :class:`Opener`."""

    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"

    def of(self, opener: Opener) -> SessionScope:
        """Build this kind of scope as a value, for a kind chosen at runtime.

        `Screen` declares its scope as a member and resolves it here, so this returns the union.
        A caller that knows the kind statically should ask the opener instead --
        `opener.user_guild()` is a `UserGuildScope`, which is what lets a `Shared[UserGuildScope]`
        pool refuse the wrong scope at the call site rather than missing at runtime. Both spellings
        build the same values, and those are the values a `SessionKey` already carries, so a panel
        holding its session key reaches a pool through `key.scope` with nothing to convert.
        """
        match self:
            case Scope.USER:
                return opener.user()
            case Scope.GUILD:
                return opener.guild()
            case Scope.USER_GUILD:
                return opener.user_guild()
            case Scope.GLOBAL:
                return opener.global_()


def _owner(opener: Opener) -> AccessPolicy:
    return Owner(opener.user_id)


@dataclass(frozen=True, slots=True)
class Screen:
    """Per-open session policy shared by every opening of one logical screen."""

    name: str
    scope: Scope = Scope.USER
    policy: SessionPolicy = DEFAULT_SESSION_POLICY
    access: Callable[[Opener], AccessPolicy] = _owner
    options: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Snapshot mount options into a read-only mapping."""
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    def key(self, opener: Opener) -> SessionKey:
        """Derive this screen's session key from an opener."""
        return SessionKey(self.name, self.scope.of(opener))

    async def open(
        self,
        sessions: SessionRegistry,
        component: Component,
        destination: Destination,
        *,
        opener: Opener,
        parent: Mount | None = None,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Construct and open or attach a mount using this screen's policy."""
        options = cast(MountOptions, {**self.options, **overrides})
        mount = sessions.defaults.mount(component, access=self.access(opener), **options)
        parent_session = None if parent is None else sessions.session_for(parent)
        if parent_session is not None:
            return await parent_session.attach(mount, destination, actor_id=opener.user_id, parent=parent)
        return await sessions.open(
            mount,
            destination,
            key=self.key(opener),
            policy=self.policy,
            actor_id=opener.user_id,
        )

    async def respond(
        self,
        sessions: SessionRegistry,
        component: Component,
        interaction: discord.Interaction[Any],
        *,
        parent: Mount | None = None,
        ephemeral: bool = True,
        wait: bool = False,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Open this screen as an interaction response."""
        return await self.open(
            sessions,
            component,
            respond_to(interaction, ephemeral=ephemeral, wait=wait),
            opener=Opener.of(interaction),
            parent=parent,
            **overrides,
        )

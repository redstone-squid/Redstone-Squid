"""Reusable per-open policy for Discord screens."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Unpack, cast

import discord

from squid_layouts.discord.access import AccessPolicy, Owner
from squid_layouts.discord.defaults import MountOptions
from squid_layouts.discord.delivery import Destination
from squid_layouts.discord.mount import Mount
from squid_layouts.discord.sessions import (
    DEFAULT_SESSION_POLICY,
    OpenResult,
    SessionKey,
    SessionPolicy,
    SessionRegistry,
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


class Scope(StrEnum):
    """Session-key scope derivable from an :class:`Opener`."""

    USER = "user"
    GUILD = "guild"
    USER_GUILD = "user_guild"
    GLOBAL = "global"


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
        match self.scope:
            case Scope.USER:
                return SessionKey.user(self.name, opener.user_id)
            case Scope.GUILD:
                return SessionKey.guild(self.name, self._require_guild(opener))
            case Scope.USER_GUILD:
                return SessionKey.user_guild(self.name, opener.user_id, self._require_guild(opener))
            case Scope.GLOBAL:
                return SessionKey.global_(self.name)

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

    @staticmethod
    def _require_guild(opener: Opener) -> int:
        if opener.guild_id is None:
            message = "guild-scoped screens require an opener with a guild"
            raise TypeError(message)
        return opener.guild_id

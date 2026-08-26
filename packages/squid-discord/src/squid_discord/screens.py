"""Reusable per-open policy for Discord screens."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Unpack, cast

import discord

from squid_discord.access import AccessPolicy, Owner
from squid_discord.defaults import MountOptions
from squid_discord.delivery import Destination, Replyable, respond_to
from squid_discord.mount import Mount
from squid_discord.sessions import (
    DEFAULT_SESSION_POLICY,
    GlobalScope,
    GuildScope,
    OpenResult,
    Rejected,
    RejectionReason,
    SessionKey,
    SessionPolicy,
    SessionRegistry,
    SessionScope,
    UserGuildScope,
    UserScope,
)
from squid_layouts.runtime.component import Component

if TYPE_CHECKING:
    from squid_discord.host import HostSource


@dataclass(frozen=True, slots=True)
class Opener:
    """Discord identity from which screen policy is derived."""

    user_id: int
    guild_id: int | None = None

    @classmethod
    def of(cls, source: discord.Interaction[Any] | Replyable) -> Opener:
        """Build an opener from an interaction, or from whatever a command arrived on.

        Read duck-typed, the way `reply_to` peeks at `ctx.interaction`: an interaction names
        its user and guild directly, a command context names an `author` and a `guild`. The
        two surfaces never meet in discord.py's type hierarchy, and a screen's policy does
        not care which one a reader arrived through.
        """
        user = getattr(source, "user", None)
        if user is None:
            user = cast(Any, source).author
        guild_id = getattr(source, "guild_id", None)
        if guild_id is None:
            guild = getattr(source, "guild", None)
            guild_id = None if guild is None else guild.id
        return cls(user.id, guild_id)

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

    def resolve(self, opener: Opener) -> SessionScope:
        """Build this kind of scope as a value, for a kind chosen at runtime.

        `ScreenSpec` declares its scope as a member and resolves it here, so this returns the union.
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


def _registry(source: SessionRegistry | HostSource) -> SessionRegistry:
    """The registry itself, or the one belonging to the host `source` is installed on."""
    if isinstance(source, SessionRegistry):
        return source
    # Imported here because a host installs a challenge presenter, which is a screen: the
    # two modules are genuinely mutually recursive, and this is the one direction that is
    # not needed at import time.
    from squid_discord.host import LayoutHost

    return LayoutHost.of(source).mounts


type ScreenOptionsResolver = Callable[[Opener], Awaitable[MountOptions]]


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    """Per-open session policy shared by every opening of one logical screen."""

    name: str
    scope: Scope = Scope.USER
    policy: SessionPolicy = DEFAULT_SESSION_POLICY
    capacity: int | None = None
    """The most members one opening of this screen admits; `None` is unbounded.

    Separate from `policy`, which governs how many sessions may occupy the key rather than
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
    access: Callable[[Opener], AccessPolicy] = _owner
    options: Mapping[str, object] = field(default_factory=dict)
    resolve_options: ScreenOptionsResolver | None = None

    def __post_init__(self) -> None:
        """Snapshot mount options into a read-only mapping."""
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    def key(self, opener: Opener) -> SessionKey:
        """Derive this screen's session key from an opener."""
        return SessionKey(self.name, self.scope.resolve(opener))

    async def _mount_options(self, opener: Opener, overrides: MountOptions) -> MountOptions:
        resolved = {} if self.resolve_options is None else await self.resolve_options(opener)
        return cast(MountOptions, {**self.options, **resolved, **overrides})

    async def open(
        self,
        component: Component,
        destination: Destination,
        *,
        sessions: SessionRegistry | HostSource,
        opener: Opener,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Construct and open a root mount using this screen's policy.

        `sessions` is the registry, or anything an installed host can be found from -- the
        interaction or command context the opening came from will do, which is what spares a
        caller holding neither from dispatching over the two invocation surfaces itself.
        """
        sessions = _registry(sessions)
        options = await self._mount_options(opener, overrides)
        mount = sessions.defaults.mount(component, access=self.access(opener), **options)
        return await sessions.open(
            mount,
            destination,
            key=self.key(opener),
            policy=self.policy,
            actor_id=opener.user_id,
            capacity=self.capacity,
            quota=self.quota,
            domain=self.domain,
        )

    async def attach(
        self,
        component: Component,
        destination: Destination,
        *,
        sessions: SessionRegistry | HostSource,
        opener: Opener,
        parent: Mount,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Construct and attach a mount below one known live parent."""
        sessions = _registry(sessions)
        parent_session = sessions.session_for(parent)
        if parent_session is None:
            return Rejected((), RejectionReason.SESSION_FINISHED)
        options = await self._mount_options(opener, overrides)
        mount = sessions.defaults.mount(component, access=self.access(opener), **options)
        return await parent_session.attach(mount, destination, actor_id=opener.user_id, parent=parent)

    async def respond(
        self,
        component: Component,
        interaction: discord.Interaction[Any],
        *,
        sessions: SessionRegistry | HostSource | None = None,
        ephemeral: bool = True,
        wait: bool = False,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Open this screen as an interaction response.

        `sessions` defaults to the registry of the host installed on the interaction's
        client. Name one only to reach a registry that is not this client's.
        """
        return await self.open(
            component,
            respond_to(interaction, ephemeral=ephemeral, wait=wait),
            sessions=interaction if sessions is None else sessions,
            opener=Opener.of(interaction),
            **overrides,
        )

    async def respond_attached(
        self,
        component: Component,
        interaction: discord.Interaction[Any],
        *,
        parent: Mount,
        sessions: SessionRegistry | HostSource | None = None,
        ephemeral: bool = True,
        wait: bool = False,
        **overrides: Unpack[MountOptions],
    ) -> OpenResult:
        """Attach this screen as an interaction response below a live parent."""
        return await self.attach(
            component,
            respond_to(interaction, ephemeral=ephemeral, wait=wait),
            sessions=interaction if sessions is None else sessions,
            opener=Opener.of(interaction),
            parent=parent,
            **overrides,
        )

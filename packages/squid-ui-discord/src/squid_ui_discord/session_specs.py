"""Reusable recipes for opening logical Discord sessions."""

from collections.abc import Awaitable, Callable, Hashable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Unpack, cast

import discord

from squid_ui.runtime.component import Component
from squid_ui_discord.access import AccessPolicy, Owner
from squid_ui_discord.delivery import MessageDestination, Replyable, respond_to
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.message_root_options import MessageRootOptions
from squid_ui_discord.sessions import (
    DEFAULT_ADMISSION,
    AdmissionSpec,
    GlobalScope,
    GuildScope,
    OpenResult,
    Rejected,
    RejectionReason,
    SessionKey,
    SessionManager,
    SessionScope,
    UserGuildScope,
    UserScope,
)

if TYPE_CHECKING:
    from squid_ui_discord.runtime import RuntimeSource


@dataclass(frozen=True, slots=True)
class OpenContext:
    """Discord identity from which screen policy is derived."""

    user_id: int
    guild_id: int | None = None

    @classmethod
    def of(cls, source: discord.Interaction[Any] | Replyable | discord.Message) -> OpenContext:
        """Build an open context from an interaction or command context.

        Read duck-typed, the way `reply_to` peeks at `ctx.interaction`: an interaction names
        its user and guild directly, a command context names an `author` and a `guild`. The
        two surfaces never meet in discord.py's type hierarchy, and a session recipe does
        not care which one a reader arrived through.
        """
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
        """Build this kind of scope as a value, for a kind chosen at runtime.

        `SessionSpec` declares its scope as a member and resolves it here, so this returns the union.
        A caller that knows the kind statically should ask the open context instead --
        `open_context.user_guild()` is a `UserGuildScope`, which is what lets a `SharedState[UserGuildScope]`
        pool refuse the wrong scope at the call site rather than missing at runtime. Both spellings
        build the same values, and those are the values a `SessionKey` already carries, so a panel
        holding its session key reaches a pool through `key.scope` with nothing to convert.
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


def _owner(open_context: OpenContext) -> AccessPolicy:
    return Owner(open_context.user_id)


def _manager(source: SessionManager | RuntimeSource) -> SessionManager:
    """The manager itself, or the one belonging to the client runtime for `source`."""
    if isinstance(source, SessionManager):
        return source
    # Imported here because a runtime installs a challenge presenter, which uses a session spec: the
    # two modules are genuinely mutually recursive, and this is the one direction that is
    # not needed at import time.
    from squid_ui_discord.runtime import ClientRuntime

    return ClientRuntime.of(source).sessions


type MessageRootOptionsResolver = Callable[[OpenContext], Awaitable[MessageRootOptions]]


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Reusable recipe shared by every opening of one logical screen."""

    name: str
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
    access: Callable[[OpenContext], AccessPolicy] = _owner
    options: Mapping[str, object] = field(default_factory=dict)
    resolve_options: MessageRootOptionsResolver | None = None

    def __post_init__(self) -> None:
        """Snapshot mount options into a read-only mapping."""
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))

    def key(self, open_context: OpenContext) -> SessionKey:
        """Derive this screen's session key from an opener."""
        return SessionKey(self.name, self.scope.resolve(open_context))

    async def _message_root_options(
        self, open_context: OpenContext, overrides: MessageRootOptions
    ) -> MessageRootOptions:
        resolved = {} if self.resolve_options is None else await self.resolve_options(open_context)
        return cast(MessageRootOptions, {**self.options, **resolved, **overrides})

    async def open(
        self,
        component: Component[Any],  # the dialect ends here: `OpenResult` exposes no typed mount
        message_destination: MessageDestination,
        *,
        sessions: SessionManager | RuntimeSource,
        open_context: OpenContext,
        key: Hashable | None = None,
        **overrides: Unpack[MessageRootOptions],
    ) -> OpenResult:
        """Construct and open a root mount using this screen's policy.

        `sessions` is the registry, or anything an installed host can be found from -- the
        interaction or command context the opening came from will do, which is what spares a
        caller holding neither from dispatching over the two invocation surfaces itself.
        """
        sessions = _manager(sessions)
        options = await self._message_root_options(open_context, overrides)
        message_root = sessions.defaults.mount(component, access=self.access(open_context), **options)
        return await sessions.open(
            message_root,
            message_destination,
            key=self.key(open_context) if key is None else key,
            admission=self.admission,
            actor_id=open_context.user_id,
            capacity=self.capacity,
            quota=self.quota,
            domain=self.domain,
        )

    async def attach(
        self,
        component: Component[Any],  # the dialect ends here: `OpenResult` exposes no typed mount
        message_destination: MessageDestination,
        *,
        sessions: SessionManager | RuntimeSource,
        open_context: OpenContext,
        parent: MessageRoot,
        **overrides: Unpack[MessageRootOptions],
    ) -> OpenResult:
        """Construct and attach a mount below one known live parent."""
        sessions = _manager(sessions)
        parent_session = sessions.session_for(parent)
        if parent_session is None:
            return Rejected((), RejectionReason.SESSION_FINISHED)
        options = await self._message_root_options(open_context, overrides)
        message_root = sessions.defaults.mount(component, access=self.access(open_context), **options)
        return await parent_session.attach(
            message_root, message_destination, actor_id=open_context.user_id, parent=parent
        )

    async def respond(
        self,
        component: Component[Any],  # the dialect ends here: `OpenResult` exposes no typed mount
        interaction: discord.Interaction[Any],
        *,
        sessions: SessionManager | RuntimeSource | None = None,
        ephemeral: bool = True,
        wait: bool = False,
        **overrides: Unpack[MessageRootOptions],
    ) -> OpenResult:
        """Open this screen as an interaction response.

        `sessions` defaults to the registry of the host installed on the interaction's
        client. Name one only to reach a registry that is not this client's.
        """
        return await self.open(
            component,
            respond_to(interaction, ephemeral=ephemeral, wait=wait),
            sessions=interaction if sessions is None else sessions,
            open_context=OpenContext.of(interaction),
            **overrides,
        )

    async def respond_attached(
        self,
        component: Component[Any],  # the dialect ends here: `OpenResult` exposes no typed mount
        interaction: discord.Interaction[Any],
        *,
        parent: MessageRoot,
        sessions: SessionManager | RuntimeSource | None = None,
        ephemeral: bool = True,
        wait: bool = False,
        **overrides: Unpack[MessageRootOptions],
    ) -> OpenResult:
        """Attach this screen as an interaction response below a live parent."""
        return await self.attach(
            component,
            respond_to(interaction, ephemeral=ephemeral, wait=wait),
            sessions=interaction if sessions is None else sessions,
            open_context=OpenContext.of(interaction),
            parent=parent,
            **overrides,
        )

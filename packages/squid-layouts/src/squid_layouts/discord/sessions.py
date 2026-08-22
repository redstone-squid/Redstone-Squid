"""Live Discord sessions, their attachment trees, and keyed cardinality policy."""

import asyncio
import logging
from collections.abc import AsyncIterator, Hashable, Iterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

import discord

from squid_layouts.discord.access import Owner
from squid_layouts.discord.delivery import Abandoned, Destination
from squid_layouts.discord.mount import Mount
from squid_layouts.runtime.component import Component

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class UserScope:
    """A session scoped to one Discord user."""

    user_id: int


@dataclass(frozen=True, slots=True)
class GuildScope:
    """A session scoped to one Discord guild."""

    guild_id: int


@dataclass(frozen=True, slots=True)
class UserGuildScope:
    """A session scoped to one user within one guild."""

    user_id: int
    guild_id: int


@dataclass(frozen=True, slots=True)
class GlobalScope:
    """A process-global session scope."""


@dataclass(frozen=True, slots=True)
class CustomScope:
    """An application-defined stable, hashable scope."""

    value: Hashable


type SessionScope = UserScope | GuildScope | UserGuildScope | GlobalScope | CustomScope


@dataclass(frozen=True, slots=True)
class SessionKey:
    """The conventional serializable spelling for a keyed logical session."""

    name: str
    scope: SessionScope

    @classmethod
    def user(cls, name: str, user_id: int) -> SessionKey:
        return cls(name, UserScope(user_id))

    @classmethod
    def guild(cls, name: str, guild_id: int) -> SessionKey:
        return cls(name, GuildScope(guild_id))

    @classmethod
    def user_guild(cls, name: str, user_id: int, guild_id: int) -> SessionKey:
        return cls(name, UserGuildScope(user_id, guild_id))

    @classmethod
    def global_(cls, name: str) -> SessionKey:
        return cls(name, GlobalScope())

    @classmethod
    def custom(cls, name: str, scope: Hashable) -> SessionKey:
        return cls(name, CustomScope(scope))


class RejectionReason(Enum):
    """Why a session request was refused before delivery."""

    COLLISION = "collision"
    PROTECTED = "protected"
    SESSION_FINISHED = "session_finished"


@dataclass(frozen=True, slots=True)
class Opened:
    """A session is live and registered."""

    session: Session


@dataclass(frozen=True, slots=True)
class Rejected:
    """Admission was refused without attempting delivery."""

    occupants: tuple[Session, ...]
    reason: RejectionReason


type OpenResult = Opened | Rejected | Abandoned


@dataclass(frozen=True, slots=True)
class OpeningRequest:
    """The context a collision or protection policy uses to judge replacement."""

    key: Hashable
    newcomer: Session
    actor_id: int | None
    required_victims: int


@dataclass(frozen=True, slots=True)
class Replace:
    """Replace these exact occupants."""

    victims: tuple[Session, ...]


@dataclass(frozen=True, slots=True)
class Refuse:
    """Keep every occupant and decline the open."""

    reason: RejectionReason = RejectionReason.COLLISION


type CollisionDecision = Replace | Refuse


class CollisionPolicy(Protocol):
    """Select exact replacement victims when a key exceeds its session limit."""

    def select(self, request: OpeningRequest, occupants: tuple[Session, ...]) -> CollisionDecision: ...


@dataclass(frozen=True, slots=True)
class Reject:
    """Reject any open that would exceed the key's limit."""

    def select(self, request: OpeningRequest, occupants: tuple[Session, ...]) -> CollisionDecision:
        return Refuse()


@dataclass(frozen=True, slots=True)
class ReplaceOldest:
    """Retire the oldest occupants needed to admit the newcomer."""

    def select(self, request: OpeningRequest, occupants: tuple[Session, ...]) -> CollisionDecision:
        return Replace(occupants[: request.required_victims])


class ReplacementProtection(Protocol):
    """Decide whether one collision victim may be retired."""

    def allows(self, request: OpeningRequest, victim: Session) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProtectCrossUserAttachments:
    """Keep sessions that another participant or attachment actor is using."""

    def allows(self, request: OpeningRequest, victim: Session) -> bool:
        actor_id = request.actor_id
        if actor_id is None:
            return not victim.participants and not victim.attachment_actors
        return not (victim.participants - {actor_id}) and not (victim.attachment_actors - {actor_id})


@dataclass(frozen=True, slots=True)
class Unprotected:
    """Allow every collision-selected replacement."""

    def allows(self, request: OpeningRequest, victim: Session) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Cardinality, collision, and replacement protection for one open."""

    limit: int | None = 1
    collision: CollisionPolicy = field(default_factory=ReplaceOldest)
    protect: ReplacementProtection = field(default_factory=ProtectCrossUserAttachments)

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            message = "session limit must be positive or None"
            raise ValueError(message)


DEFAULT_SESSION_POLICY = SessionPolicy()


class Session:
    """One root mount and every child mount in its operational lifetime."""

    def __init__(
        self,
        registry: SessionRegistry,
        root: Mount,
        *,
        key: Hashable | None,
        actor_id: int | None,
    ) -> None:
        self.key = key
        self.root = root
        self._registry = registry
        self._mounts: list[Mount] = [root]
        self._parent: dict[Mount, Mount | None] = {root: None}
        self._actor: dict[Mount, int | None] = {root: actor_id}
        self._participants = frozenset() if actor_id is None else frozenset({actor_id})
        self._lifecycle_lock = asyncio.Lock()
        self._finishing = False
        self._closed = False

    @property
    def mounts(self) -> tuple[Mount, ...]:
        """The root followed by attached mounts in registration order."""
        return tuple(self._mounts)

    @property
    def participants(self) -> frozenset[int]:
        """Operational actors already attributed to this session."""
        return self._participants

    @property
    def attachment_actors(self) -> frozenset[int]:
        """Actors attributed to non-root mounts, for replacement protection."""
        return frozenset(actor for mount, actor in self._actor.items() if mount is not self.root and actor is not None)

    async def attach(
        self,
        mount: Mount,
        destination: Destination,
        *,
        actor_id: int | None = None,
        parent: Mount | None = None,
    ) -> OpenResult:
        """Deliver and attach a child mount to this session."""
        async with self._lifecycle_lock:
            if self._closed or self.root.finished:
                return Rejected((self,), RejectionReason.SESSION_FINISHED)
            parent = self.root if parent is None else parent
            if parent not in self._parent or parent.finished:
                return Rejected((self,), RejectionReason.SESSION_FINISHED)
            result = await mount.send(destination)
            if isinstance(result, Abandoned):
                return result
            self._mounts.append(mount)
            self._parent[mount] = parent
            self._actor[mount] = actor_id
            self._registry._index_mount(self, mount)
            mount.on_finish(self._mount_finished)
            return Opened(self)

    async def finish(self, *, disable: bool = True) -> None:
        """Finish every mount depth-first and unregister the session."""
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._finishing = True
            try:
                await self._finish_mounts(self._depth_first(self.root), disable=disable)
            finally:
                self._registry._forget(self)
                self._closed = True
                self._finishing = False

    def _activate(self) -> None:
        self._registry._index_mount(self, self.root)
        self.root.on_finish(self._mount_finished)

    async def _mount_finished(self, mount: Mount) -> None:
        if self._finishing or self._closed:
            return
        async with self._lifecycle_lock:
            if self._finishing or self._closed or mount not in self._parent:
                return
            self._finishing = True
            try:
                if mount is self.root:
                    descendants = tuple(
                        candidate for candidate in self._depth_first(self.root) if candidate is not mount
                    )
                    await self._finish_mounts(descendants)
                    self._registry._forget(self)
                    self._closed = True
                    return
                branch = self._depth_first(mount)
                await self._finish_mounts(tuple(candidate for candidate in branch if candidate is not mount))
                self._detach(branch)
            finally:
                self._finishing = False

    def _depth_first(self, root: Mount) -> tuple[Mount, ...]:
        ordered: list[Mount] = []

        def visit(parent: Mount) -> None:
            for child in tuple(self._mounts):
                if self._parent.get(child) is parent:
                    visit(child)
            ordered.append(parent)

        visit(root)
        return tuple(ordered)

    async def _finish_mounts(self, mounts: Sequence[Mount], *, disable: bool = True) -> None:
        for mount in mounts:
            try:
                await mount.finish(disable=disable)
            except Exception:
                logger.exception("could not finish mount %s in session %r", mount.id, self.key)

    def _detach(self, branch: Sequence[Mount]) -> None:
        for mount in branch:
            self._registry._unindex_mount(self, mount)
            self._parent.pop(mount, None)
            self._actor.pop(mount, None)
            if mount in self._mounts:
                self._mounts.remove(mount)


class SessionRegistry:
    """The live logical sessions owned by this process."""

    def __init__(self) -> None:
        self._by_key: dict[Hashable, list[Session]] = {}
        self._by_mount: dict[Mount, Session] = {}
        self._sessions: list[Session] = []
        self._locks: dict[Hashable, asyncio.Lock] = {}
        self._waiting: dict[Hashable, int] = {}

    async def open(
        self,
        mount: Mount,
        destination: Destination,
        *,
        key: Hashable | None = None,
        policy: SessionPolicy = DEFAULT_SESSION_POLICY,
        actor_id: int | None = None,
    ) -> OpenResult:
        """Admit, deliver, and register one new root session."""
        if key is None:
            return await self._open_locked(mount, destination, key=None, policy=policy, actor_id=actor_id)
        async with self._lock_for(key):
            return await self._open_locked(mount, destination, key=key, policy=policy, actor_id=actor_id)

    def get(self, key: Hashable) -> tuple[Session, ...]:
        """Return every session currently occupying `key`, oldest first."""
        return tuple(self._by_key.get(key, ()))

    def session_for(self, mount: Mount) -> Session | None:
        """Return the logical session that owns `mount`, if this registry knows it."""
        return self._by_mount.get(mount)

    async def close(self, key: Hashable, *, disable: bool = True) -> None:
        """Finish every session under `key`."""
        for session in self.get(key):
            await session.finish(disable=disable)

    async def close_all(self, *, disable: bool = True) -> None:
        """Finish every session, isolating one teardown failure from the rest."""
        for session in tuple(self._sessions):
            try:
                await session.finish(disable=disable)
            except Exception:
                logger.exception("could not close session %r", session.key)

    def active(self) -> Iterator[Session]:
        """Yield every live session in registration order."""
        yield from tuple(self._sessions)

    async def _open_locked(
        self,
        mount: Mount,
        destination: Destination,
        *,
        key: Hashable | None,
        policy: SessionPolicy,
        actor_id: int | None,
    ) -> OpenResult:
        occupants = () if key is None else self._live_occupants(key)
        newcomer = Session(self, mount, key=key, actor_id=actor_id)
        victims: tuple[Session, ...] = ()
        if key is not None and policy.limit is not None and len(occupants) >= policy.limit:
            required = len(occupants) + 1 - policy.limit
            request = OpeningRequest(key, newcomer, actor_id, required)
            decision = policy.collision.select(request, occupants)
            if isinstance(decision, Refuse):
                return Rejected(occupants, decision.reason)
            victims = decision.victims
            if (
                len(victims) != required
                or len(set(victims)) != len(victims)
                or any(victim not in occupants for victim in victims)
            ):
                message = "collision policy must select the exact required occupants"
                raise ValueError(message)
            if any(not policy.protect.allows(request, victim) for victim in victims):
                return Rejected(occupants, RejectionReason.PROTECTED)

        result = await mount.send(destination)
        if isinstance(result, Abandoned):
            return result

        self._sessions.append(newcomer)
        if key is not None:
            self._by_key.setdefault(key, []).append(newcomer)
        newcomer._activate()
        for victim in victims:
            await victim.finish()
        return Opened(newcomer)

    def _live_occupants(self, key: Hashable) -> tuple[Session, ...]:
        occupants = self._by_key.get(key, [])
        for session in tuple(occupants):
            if session.root.finished:
                logger.warning("session %s held a finished root; discarding it", key)
                self._forget(session)
        return tuple(self._by_key.get(key, ()))

    def _index_mount(self, session: Session, mount: Mount) -> None:
        self._by_mount[mount] = session

    def _unindex_mount(self, session: Session, mount: Mount) -> None:
        if self._by_mount.get(mount) is session:
            del self._by_mount[mount]

    def _forget(self, session: Session) -> None:
        if session in self._sessions:
            self._sessions.remove(session)
        if session.key is not None:
            occupants = self._by_key.get(session.key)
            if occupants is not None and session in occupants:
                occupants.remove(session)
                if not occupants:
                    del self._by_key[session.key]
        for mount in session.mounts:
            self._unindex_mount(session, mount)

    @asynccontextmanager
    async def _lock_for(self, key: Hashable) -> AsyncIterator[None]:
        """Serialize opens on one key, keeping no idle lock objects."""
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._waiting[key] - 1
            if remaining:
                self._waiting[key] = remaining
            else:
                del self._waiting[key]
                del self._locks[key]


async def open_personal(
    sessions: SessionRegistry,
    component: Component,
    interaction: discord.Interaction,
    *,
    key: Hashable,
    policy: SessionPolicy = DEFAULT_SESSION_POLICY,
    **mount_options: Any,
) -> OpenResult:
    """Open the common owner-only, ephemeral interaction session path."""
    mount = Mount(component, access=Owner(interaction.user.id), **mount_options)
    from squid_layouts.discord.delivery import respond_to

    return await sessions.open(
        mount,
        respond_to(interaction, ephemeral=True),
        key=key,
        policy=policy,
        actor_id=interaction.user.id,
    )

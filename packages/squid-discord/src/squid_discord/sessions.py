"""Live Discord sessions, their attachment trees, and keyed cardinality policy."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Hashable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum, StrEnum
from typing import Protocol
from uuid import uuid4

from squid_discord.defaults import MountDefaults
from squid_discord.delivery import Abandoned, Delivered, Destination
from squid_discord.mount import Mount

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
    ADMISSION_BUSY = "admission_busy"
    QUOTA_REACHED = "quota_reached"


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    """Immutable facts a session policy may use during local admission.

    ``id`` and ``opened_at`` do not change over a session's lifetime. Membership fields
    are a point-in-time copy too: the registry creates a fresh snapshot for a decision after
    a membership or attachment change while retaining the same identity and opening time.
    """

    id: str
    opened_at: datetime
    key: Hashable | None
    actor_id: int | None
    durable: bool = False
    local: bool = True
    members: frozenset[int] = frozenset()
    attachment_actors: frozenset[int] = frozenset()
    capacity: int | None = None

    @property
    def participants(self) -> frozenset[int]:
        """Every user attributable to this session, however they arrived.

        Derived rather than stored so a policy reads one field: explicit members, actors
        attributed to attached mounts, and the opener are all reasons to treat a session as
        somebody's. A policy that wants only one of them reads that field directly.
        """
        opener = frozenset() if self.actor_id is None else frozenset({self.actor_id})
        return self.members | self.attachment_actors | opener

    @property
    def remaining_capacity(self) -> int | None:
        """Free member slots, or `None` when this session is unbounded."""
        return None if self.capacity is None else max(self.capacity - len(self.members), 0)

    @property
    def is_durable(self) -> bool:
        """Whether the session has durable ownership/state."""
        return self.durable

    @property
    def is_local(self) -> bool:
        """Whether this process owns the live session."""
        return self.local


@dataclass(frozen=True, slots=True)
class Opened[SessionT]:
    """A session is live and registered."""

    session: SessionT


@dataclass(frozen=True, slots=True)
class Rejected:
    """Admission was refused without attempting delivery."""

    occupants: tuple[SessionSnapshot, ...]
    reason: RejectionReason


type OpenResult = Opened | Rejected | Abandoned
type BeforeRegistration = Callable[[Session, Delivered, tuple[SessionSnapshot, ...]], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class OpeningRequest:
    """The context a collision or protection policy uses to judge replacement."""

    key: Hashable
    newcomer: SessionSnapshot
    actor_id: int | None
    required_victims: int


@dataclass(frozen=True, slots=True)
class Replace:
    """Replace these exact occupants."""

    victims: tuple[SessionSnapshot, ...]


@dataclass(frozen=True, slots=True)
class Refuse:
    """Keep every occupant and decline the open."""

    reason: RejectionReason = RejectionReason.COLLISION


type CollisionDecision = Replace | Refuse


class CollisionPolicy(Protocol):
    """Asynchronously select exact replacement victims from immutable snapshots."""

    async def select(self, request: OpeningRequest, occupants: tuple[SessionSnapshot, ...]) -> CollisionDecision: ...


@dataclass(frozen=True, slots=True)
class Reject:
    """Reject any open that would exceed the key's limit."""

    async def select(self, request: OpeningRequest, occupants: tuple[SessionSnapshot, ...]) -> CollisionDecision:
        return Refuse()


@dataclass(frozen=True, slots=True)
class ReplaceOldest:
    """Retire the oldest occupants needed to admit the newcomer."""

    async def select(self, request: OpeningRequest, occupants: tuple[SessionSnapshot, ...]) -> CollisionDecision:
        return Replace(occupants[: request.required_victims])


class ReplacementPolicy(Protocol):
    """Asynchronously decide whether one immutable collision victim may be retired."""

    async def allows(self, request: OpeningRequest, victim: SessionSnapshot) -> bool: ...


@dataclass(frozen=True, slots=True)
class ProtectCrossUserAttachments:
    """Keep sessions that another participant or attachment actor is using."""

    async def allows(self, request: OpeningRequest, victim: SessionSnapshot) -> bool:
        actor_id = request.actor_id
        others = victim.participants if actor_id is None else victim.participants - {actor_id}
        allowed = not others
        # A temporary open must not silently retire a durable session. Hosts that
        # intentionally perform that operation can supply ``Unprotected`` or their own
        # protection policy.
        return allowed and (request.newcomer.durable or not victim.durable)


@dataclass(frozen=True, slots=True)
class Unprotected:
    """Allow every collision-selected replacement."""

    async def allows(self, request: OpeningRequest, victim: SessionSnapshot) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Cardinality, collision, and replacement protection for one open."""

    limit: int | None = 1
    collision: CollisionPolicy = field(default_factory=ReplaceOldest)
    protect: ReplacementPolicy = field(default_factory=ProtectCrossUserAttachments)

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit <= 0:
            message = "session limit must be positive or None"
            raise ValueError(message)


DEFAULT_SESSION_POLICY = SessionPolicy()


class MembershipStatus(StrEnum):
    """The outcome of one membership operation."""

    JOINED = "joined"
    ALREADY_MEMBER = "already_member"
    LEFT = "left"
    NOT_MEMBER = "not_member"
    AT_CAPACITY = "at_capacity"
    QUOTA_REACHED = "quota_reached"
    REFUSED = "refused"
    CONFLICT = "conflict"
    SESSION_FINISHED = "session_finished"


@dataclass(frozen=True, slots=True)
class MembershipResult:
    """What one `join` or `leave` decided, with the set it decided against."""

    user_id: int
    status: MembershipStatus
    members: frozenset[int]
    remaining_capacity: int | None

    @property
    def committed(self) -> bool:
        """Whether this operation changed the membership set."""
        return self.status in (MembershipStatus.JOINED, MembershipStatus.LEFT)


@dataclass(frozen=True, slots=True)
class _MemberScope:
    """The lock identity one user's membership operations serialize on."""

    user_id: int


def _require_user_id(user_id: int) -> int:
    """Reject the two shapes a Discord id is never allowed to take."""
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        message = "member ids must be positive integers"
        raise ValueError(message)
    return user_id


@dataclass(frozen=True, slots=True)
class _Membership:
    """One mount's place in a session: under which parent, and attributed to whom.

    Attaching a mount used to write a list and two parallel dicts that had to stay in step
    by inspection, and detaching had to unwind all three. One record per mount makes the
    session's ownership a single entry that is either there or not.
    """

    parent: Mount | None
    """`None` for the root, which the session holds directly."""
    actor: int | None
    """Operational attribution, not membership -- see `Session.join`."""


class Session:
    """One root mount and every child mount in its operational lifetime."""

    def __init__(
        self,
        registry: SessionRegistry,
        root: Mount,
        *,
        key: Hashable | None,
        actor_id: int | None,
        durable: bool = False,
        local: bool = True,
        snapshot: SessionSnapshot | None = None,
        members: frozenset[int] | None = None,
        capacity: int | None = None,
        quota: int | None = None,
        domain: str | None = None,
    ) -> None:
        if capacity is not None and capacity <= 0:
            message = "session capacity must be positive or None"
            raise ValueError(message)
        if quota is not None and quota <= 0:
            message = "session quota must be positive or None"
            raise ValueError(message)
        domain = domain or (key.name if isinstance(key, SessionKey) else None)
        if quota is not None and domain is None:
            message = "a session quota needs a membership domain; pass domain= or key a SessionKey"
            raise ValueError(message)
        self.key = key
        self.root = root
        self._registry = registry
        self._graph: dict[Mount, _Membership] = {root: _Membership(parent=None, actor=actor_id)}
        # The opener is the initial member: a semantic default that makes their presence
        # explicit, not an authorization decision. Recovery supplies the stored set instead.
        if members is None:
            members = frozenset() if actor_id is None else frozenset({_require_user_id(actor_id)})
        self._members = members
        self._capacity = capacity
        self._quota = quota
        self._domain = domain
        self._snapshot = snapshot or SessionSnapshot(
            id=str(uuid4()),
            opened_at=datetime.now(UTC),
            key=key,
            actor_id=actor_id,
            durable=durable,
            local=local,
        )
        self._lifecycle_lock = asyncio.Lock()
        self._finishing = False
        self._closed = False

    @property
    def mounts(self) -> tuple[Mount, ...]:
        """The root followed by attached mounts in registration order."""
        return tuple(self._graph)

    @property
    def members(self) -> frozenset[int]:
        """The users explicitly admitted to this session."""
        return self._members

    @property
    def participants(self) -> frozenset[int]:
        """Every user attributable to this session; see `SessionSnapshot.participants`."""
        return self.snapshot.participants

    @property
    def capacity(self) -> int | None:
        """The most members this session admits, or `None` when unbounded."""
        return self._capacity

    @property
    def remaining_capacity(self) -> int | None:
        """Free member slots, or `None` when this session is unbounded."""
        return None if self._capacity is None else max(self._capacity - len(self._members), 0)

    @property
    def quota(self) -> int | None:
        """The most sessions in this domain one member may hold, or `None` for no limit."""
        return self._quota

    @property
    def domain(self) -> str | None:
        """The membership family this session belongs to; `key.name` unless overridden."""
        return self._domain

    def has_member(self, user_id: int) -> bool:
        """Whether `user_id` is an explicit member of this session."""
        return user_id in self._members

    @property
    def attachment_actors(self) -> frozenset[int]:
        """Actors attributed to non-root mounts, for replacement protection."""
        return frozenset(
            membership.actor
            for mount, membership in self._graph.items()
            if mount is not self.root and membership.actor is not None
        )

    def parent_of(self, mount: Mount) -> Mount | None:
        """Return the mount's parent, or `None` for the root or an unknown mount."""
        membership = self._graph.get(mount)
        return None if membership is None else membership.parent

    def actor_for(self, mount: Mount) -> int | None:
        """Return the actor attributed to one mount in this session."""
        membership = self._graph.get(mount)
        return None if membership is None else membership.actor

    @property
    def snapshot(self) -> SessionSnapshot:
        """Return immutable current facts for admission and inspection."""
        return SessionSnapshot(
            id=self._snapshot.id,
            opened_at=self._snapshot.opened_at,
            key=self.key,
            actor_id=self._snapshot.actor_id,
            durable=self._snapshot.durable,
            local=self._snapshot.local,
            members=self._members,
            attachment_actors=self.attachment_actors,
            capacity=self._capacity,
        )

    @property
    def id(self) -> str:
        """Stable identity assigned when this session opened."""
        return self._snapshot.id

    @property
    def opened_at(self) -> datetime:
        """UTC timestamp at which this session opened."""
        return self._snapshot.opened_at

    @property
    def durable(self) -> bool:
        """Whether this session is backed by durable state."""
        return self._snapshot.durable

    @property
    def local(self) -> bool:
        """Whether this process owns the live session."""
        return self._snapshot.local

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
                return Rejected((self.snapshot,), RejectionReason.SESSION_FINISHED)
            parent = self.root if parent is None else parent
            if parent not in self._graph or parent.finished:
                return Rejected((self.snapshot,), RejectionReason.SESSION_FINISHED)
            result = await mount.send(destination)
            if isinstance(result, Abandoned):
                return result
            self._graph[mount] = _Membership(parent=parent, actor=actor_id)
            self._registry._index_mount(self, mount)
            mount.on_finish(self._mount_finished)
            return Opened(self)

    async def join(
        self,
        user_id: int,
        *,
        when: Callable[[frozenset[int]], bool] | None = None,
        expect: frozenset[int] | None = None,
    ) -> MembershipResult:
        """Admit `user_id` to this session under its capacity.

        Membership is caller-authorized: the framework decides capacity and atomicity, never
        whether a user was invited, banned, paid, or assigned to a team. Perform that check
        before calling.

        `when` is an extra rule over the current members, evaluated under the session
        lifecycle lock. **It must be synchronous and pure** — that lock also serialises
        `attach` and `finish`, so I/O inside it stalls the whole session. A rule that is both
        asynchronous and set-dependent belongs outside the lock instead: read `members`, make
        the async decision, then commit with `expect=` and retry on `CONFLICT`.
        """
        _require_user_id(user_id)
        # Outside the lifecycle lock and before it, because a quota spans sessions: this one
        # cannot answer for the others. Every path that admits a member takes it first, so
        # one user's concurrent joins serialize while unrelated users never contend.
        async with self._registry._member_lock(user_id), self._lifecycle_lock:
            if (dead := self._membership_refusal(user_id, expect)) is not None:
                return dead
            if user_id in self._members:
                return self._membership(user_id, MembershipStatus.ALREADY_MEMBER)
            if (remaining := self.remaining_capacity) is not None and remaining <= 0:
                return self._membership(user_id, MembershipStatus.AT_CAPACITY)
            if self._quota_reached(user_id):
                return self._membership(user_id, MembershipStatus.QUOTA_REACHED)
            if when is not None and not when(self._members):
                return self._membership(user_id, MembershipStatus.REFUSED)
            self._members = self._members | {user_id}
            return self._membership(user_id, MembershipStatus.JOINED)

    async def leave(self, user_id: int, *, expect: frozenset[int] | None = None) -> MembershipResult:
        """Remove `user_id` from this session.

        The opener and the final member may both leave. An empty session stays alive; only
        the application knows when a lobby or game is over.
        """
        _require_user_id(user_id)
        async with self._lifecycle_lock:
            if (dead := self._membership_refusal(user_id, expect)) is not None:
                return dead
            if user_id not in self._members:
                return self._membership(user_id, MembershipStatus.NOT_MEMBER)
            self._members = self._members - {user_id}
            return self._membership(user_id, MembershipStatus.LEFT)

    def _membership_refusal(self, user_id: int, expect: frozenset[int] | None) -> MembershipResult | None:
        """The two refusals both operations share, checked under the lifecycle lock."""
        if self._closed or self.root.finished:
            return self._membership(user_id, MembershipStatus.SESSION_FINISHED)
        if expect is not None and expect != self._members:
            return self._membership(user_id, MembershipStatus.CONFLICT)
        return None

    def _quota_reached(self, user_id: int, *, excluding: Collection[Session] = ()) -> bool:
        """Whether admitting `user_id` here would exceed this domain's per-user quota.

        Counted by scanning the registry's live sessions rather than from a maintained index:
        a scan cannot go stale when a session finishes, loses its claim, or is recovered, and
        the population is small. `user_id` is never a member of this session yet, so this one
        is not among those counted, and neither is anything in `excluding`.
        """
        if self._quota is None:
            return False
        held = self._registry.sessions_for_member(user_id, domain=self._domain, excluding=excluding)
        return len(held) >= self._quota

    def _membership(self, user_id: int, status: MembershipStatus) -> MembershipResult:
        return MembershipResult(user_id, status, self._members, self.remaining_capacity)

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

    def _attach_existing(self, mount: Mount, *, parent: Mount, actor_id: int | None) -> None:
        """Attach an already presented mount while reconstructing a session."""
        if parent not in self._graph:
            message = "recovered attachment parent is not in the session"
            raise ValueError(message)
        self._graph[mount] = _Membership(parent=parent, actor=actor_id)
        self._registry._index_mount(self, mount)
        mount.on_finish(self._mount_finished)

    async def _mount_finished(self, mount: Mount) -> None:
        if self._finishing or self._closed:
            return
        async with self._lifecycle_lock:
            if self._finishing or self._closed or mount not in self._graph:
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
            for child, membership in tuple(self._graph.items()):
                if membership.parent is parent:
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
            # The registry is a separate owner of the same mount, so its index is dropped
            # separately rather than folded into the membership record.
            self._registry._unindex_mount(self, mount)
            self._graph.pop(mount, None)


def _resolve_victims(selected: tuple[SessionSnapshot, ...], occupants: tuple[Session, ...]) -> tuple[Session, ...]:
    """Resolve snapshot victims back to live sessions by their stable identity."""
    by_id = {occupant.id: occupant for occupant in occupants}
    return tuple(by_id[victim.id] for victim in selected if victim.id in by_id)


class SessionRegistry:
    """The live logical sessions owned by this process."""

    def __init__(self, defaults: MountDefaults = MountDefaults()) -> None:  # noqa: B008  # frozen value
        self.defaults = defaults
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
        durable: bool = False,
        local: bool = True,
        snapshot: SessionSnapshot | None = None,
        capacity: int | None = None,
        quota: int | None = None,
        domain: str | None = None,
    ) -> OpenResult:
        """Admit, deliver, and register one new root session.

        ``durable`` and ``local`` are descriptive admission facts.  A caller restoring a
        session may pass its immutable ``snapshot`` instead to retain the original identity
        and opening time.  ``capacity`` caps the session's explicit members; it is a fact of
        this session rather than of the key contest, so it is not part of ``policy``.
        """
        if not isinstance(mount, Mount):
            message = "SessionRegistry.open requires a Mount; use MountDefaults.mount or ScreenSpec.open"
            raise TypeError(message)

        if key is None:
            return await self._open_locked(
                mount,
                destination,
                key=None,
                policy=policy,
                actor_id=actor_id,
                durable=durable,
                local=local,
                snapshot=snapshot,
                capacity=capacity,
                quota=quota,
                domain=domain,
                remote_occupants=(),
                before_registration=None,
                session_type=Session,
            )
        async with self._lock_for(key):
            return await self._open_locked(
                mount,
                destination,
                key=key,
                policy=policy,
                actor_id=actor_id,
                durable=durable,
                local=local,
                snapshot=snapshot,
                capacity=capacity,
                quota=quota,
                domain=domain,
                remote_occupants=(),
                before_registration=None,
                session_type=Session,
            )

    async def _open_coordinated[SessionT: Session](
        self,
        mount: Mount,
        destination: Destination,
        *,
        key: SessionKey,
        policy: SessionPolicy,
        actor_id: int | None,
        snapshot: SessionSnapshot,
        remote_occupants: tuple[SessionSnapshot, ...],
        before_registration: BeforeRegistration,
        session_type: type[SessionT],
        capacity: int | None = None,
        quota: int | None = None,
        domain: str | None = None,
    ) -> Opened[SessionT] | Rejected | Abandoned:
        """Open under the registry lock with a caller-owned external commit boundary.

        Durability uses this to keep the incumbent until promotion and fenced persistence
        both succeed. It is deliberately package-private: ordinary hosts should call `open`.
        """
        async with self._lock_for(key):
            return await self._open_locked(
                mount,
                destination,
                key=key,
                policy=policy,
                actor_id=actor_id,
                durable=True,
                local=True,
                snapshot=snapshot,
                capacity=capacity,
                quota=quota,
                domain=domain,
                remote_occupants=remote_occupants,
                before_registration=before_registration,
                session_type=session_type,
            )

    def _register_recovered[SessionT: Session](
        self,
        root: Mount,
        *,
        key: SessionKey,
        actor_id: int | None,
        snapshot: SessionSnapshot,
        attachments: tuple[tuple[Mount, Mount, int | None], ...],
        session_type: type[SessionT],
        members: frozenset[int] | None = None,
        capacity: int | None = None,
        quota: int | None = None,
        domain: str | None = None,
    ) -> SessionT:
        """Register a frontend-reconnected session without delivering its mounts again."""
        session = session_type(
            self,
            root,
            key=key,
            actor_id=actor_id,
            durable=True,
            local=True,
            snapshot=snapshot,
            members=members,
            capacity=capacity,
            quota=quota,
            domain=domain,
        )
        self._sessions.append(session)
        self._by_key.setdefault(key, []).append(session)
        session._activate()
        for mount, parent, mount_actor_id in attachments:
            session._attach_existing(mount, parent=parent, actor_id=mount_actor_id)
        return session

    def get(self, key: Hashable) -> tuple[Session, ...]:
        """Return every session currently occupying `key`, oldest first."""
        return tuple(self._by_key.get(key, ()))

    def session_for(self, mount: Mount) -> Session | None:
        """Return the logical session that owns `mount`, if this registry knows it."""
        return self._by_mount.get(mount)

    def sessions_for_member(
        self, user_id: int, *, domain: str | None = None, excluding: Collection[Session] = ()
    ) -> tuple[Session, ...]:
        """Every live session `user_id` is an explicit member of, oldest first.

        Restrict to one membership family with `domain`. This answers "what is this user in
        right now?" for quotas, for a "you are already in a game" affordance, and for
        devtools.

        `excluding` answers it as of a pending replacement instead: sessions being retired are
        still registered while their replacement is being opened, and counting them would count
        a seat twice.
        """
        retiring = {session.id for session in excluding}
        return tuple(
            session
            for session in self._sessions
            if user_id in session.members
            and (domain is None or session.domain == domain)
            and session.id not in retiring
        )

    def _member_lock(self, user_id: int) -> AbstractAsyncContextManager[None]:
        """Serialize one user's admissions across every session in this registry."""
        return self._lock_for(_MemberScope(user_id))

    def find(self, session_id: str) -> Session | None:
        """Return a live session by its stable diagnostic identity."""
        return next((session for session in self._sessions if session.id == session_id), None)

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
        durable: bool,
        local: bool,
        snapshot: SessionSnapshot | None,
        capacity: int | None,
        quota: int | None,
        domain: str | None,
        remote_occupants: tuple[SessionSnapshot, ...],
        before_registration: BeforeRegistration | None,
        session_type: type[Session],
    ) -> OpenResult:
        occupants = () if key is None else self._live_occupants(key)
        local_summaries = tuple(session.snapshot for session in occupants)
        local_ids = {snapshot.id for snapshot in local_summaries}
        snapshots = (*local_summaries, *(snapshot for snapshot in remote_occupants if snapshot.id not in local_ids))
        snapshots = tuple(sorted(snapshots, key=lambda occupant: (occupant.opened_at, occupant.id)))
        newcomer = session_type(
            self,
            mount,
            key=key,
            actor_id=actor_id,
            durable=durable,
            local=local,
            snapshot=snapshot,
            capacity=capacity,
            quota=quota,
            domain=domain,
        )
        victims: tuple[Session, ...] = ()
        selected: tuple[SessionSnapshot, ...] = ()
        if key is not None and policy.limit is not None and len(snapshots) >= policy.limit:
            required = len(snapshots) + 1 - policy.limit
            request = OpeningRequest(key, newcomer.snapshot, actor_id, required)
            decision = await policy.collision.select(request, snapshots)
            if isinstance(decision, Refuse):
                return Rejected(snapshots, decision.reason)
            selected = decision.victims
            victims = _resolve_victims(selected, occupants)
            summary_ids = {occupant.id for occupant in snapshots}
            if (
                len(selected) != required
                or len({victim.id for victim in selected}) != len(selected)
                or any(victim.id not in summary_ids for victim in selected)
            ):
                message = "collision policy must select the exact required occupants"
                raise ValueError(message)
            for victim in selected:
                if not await policy.protect.allows(request, victim):
                    return Rejected(snapshots, RejectionReason.PROTECTED)

        # Advisory: the authoritative check is under the member lock below, but refusing
        # here means a doomed open costs no Discord message. Counted against the membership
        # this open will leave behind, because the victims below are still registered and a
        # caller replacing their own session would otherwise be counted in two seats at once.
        if actor_id is not None and newcomer._quota_reached(actor_id, excluding=victims):
            return Rejected(snapshots, RejectionReason.QUOTA_REACHED)

        # Indexed before delivery, because delivery renders: a root component that draws
        # session facts would otherwise find no session on its own first paint and never be
        # asked to draw again. An abandoned delivery takes the entry back out.
        self._index_mount(newcomer, mount)
        result = await mount.send(destination)
        if isinstance(result, Abandoned):
            self._unindex_mount(newcomer, mount)
            return result

        # The opener joins by opening, so a quota has to bind here too — otherwise it is
        # evaded by opening a second session rather than joining one. Held from before the
        # external commit through registration, so the count and the append are one step.
        async with AsyncExitStack() as commit:
            if actor_id is not None:
                await commit.enter_async_context(self._member_lock(actor_id))
                if newcomer._quota_reached(actor_id, excluding=victims):
                    self._unindex_mount(newcomer, mount)
                    await mount.finish()
                    return Rejected(snapshots, RejectionReason.QUOTA_REACHED)

            if before_registration is not None:
                try:
                    await before_registration(newcomer, result, selected)
                except BaseException:
                    await newcomer.finish()
                    raise

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

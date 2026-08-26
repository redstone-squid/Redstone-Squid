"""Fenced admission, recovery, and checkpoint supervision for durable sessions."""

import asyncio
import json
import logging
import math
import secrets
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

import anyio
from anyio.abc import TaskStatus

from squid_storage import ClaimToken, DurableSessionStore, SessionRecord
from squid_ui_discord.delivery import Abandoned, Delivered, MessageDestination
from squid_ui_discord.message_root import MessageRoot
from squid_ui_discord.sessions import (
    DEFAULT_ADMISSION,
    AdmissionSpec,
    MembershipResult,
    Opened,
    Rejected,
    RejectionReason,
    Session,
    SessionKey,
    SessionManager,
    SessionSnapshot,
)

from . import ComponentRegistry, FrontendAddress, MessageRootStateError, RestoreContext
from .frontend import (
    DurableFrontend,
    Missing,
    NotDurable,
    Promoted,
    Reconnected,
    RecoveredBinding,
    Unreachable,
)
from .session_records import (
    DurableSessionCodec,
    DurableSessionRecord,
    SessionRootRecord,
    decode_session_key,
    encode_session_key,
    encode_session_scope,
)

logger = logging.getLogger(__name__)


class DurabilityHealth(StrEnum):
    """Current persistence health of one locally owned durable session."""

    HEALTHY = "healthy"
    CHECKPOINT_PENDING = "checkpoint_pending"
    CLAIM_LOST = "claim_lost"


@dataclass(frozen=True, slots=True)
class RecoveryItem:
    """Sanitized result for one durable record in a recovery sweep."""

    record_key: str
    session_key: SessionKey | None
    addresses: tuple[FrontendAddress, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    """Per-record recovery outcomes; one bad record never aborts the sweep."""

    restored: tuple[RecoveryItem, ...] = ()
    missing: tuple[RecoveryItem, ...] = ()
    expired: tuple[RecoveryItem, ...] = ()
    unreachable: tuple[RecoveryItem, ...] = ()
    incompatible: tuple[RecoveryItem, ...] = ()
    failed: tuple[RecoveryItem, ...] = ()
    claimed_elsewhere: tuple[RecoveryItem, ...] = ()


@dataclass(frozen=True, slots=True)
class DurableRuntimeSnapshot:
    """Operational state retained by a supervised durable runtime."""

    running: bool
    shutting_down: bool
    active: tuple[tuple[str, DurabilityHealth, int], ...]
    dirty: tuple[str, ...]
    last_recovery: RecoveryReport | None


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """Result of one explicitly requested durable-record purge."""

    record_key: str
    deleted: bool
    reason: str


@dataclass(slots=True)
class _RecoveryReportBuilder:
    restored: list[RecoveryItem] = field(default_factory=list)
    missing: list[RecoveryItem] = field(default_factory=list)
    expired: list[RecoveryItem] = field(default_factory=list)
    unreachable: list[RecoveryItem] = field(default_factory=list)
    incompatible: list[RecoveryItem] = field(default_factory=list)
    failed: list[RecoveryItem] = field(default_factory=list)
    claimed_elsewhere: list[RecoveryItem] = field(default_factory=list)

    def freeze(self) -> RecoveryReport:
        return RecoveryReport(
            restored=tuple(self.restored),
            missing=tuple(self.missing),
            expired=tuple(self.expired),
            unreachable=tuple(self.unreachable),
            incompatible=tuple(self.incompatible),
            failed=tuple(self.failed),
            claimed_elsewhere=tuple(self.claimed_elsewhere),
        )


@dataclass(slots=True)
class _ActiveSession:
    session: DurableSession
    token: ClaimToken
    expires_at: float | None
    message_root_ids: dict[MessageRoot, str]
    recipes: dict[MessageRoot, str]
    addresses: dict[MessageRoot, FrontendAddress]
    health: DurabilityHealth = DurabilityHealth.HEALTHY
    # Capture and save together, so a slower writer cannot land its older snapshot after a
    # newer one. Membership makes this ordinary: joins are far more frequent than attaches.
    writes: asyncio.Lock = field(default_factory=asyncio.Lock)


class _NotDurableOpen(Exception):
    def __init__(self, result: NotDurable) -> None:
        super().__init__(result.reason)
        self.result = result


class DurableSession(Session):
    """A logical session whose complete mount graph is owned by a durable runtime."""

    _durable_runtime: DurableSessionRuntime | None = None

    @property
    def health(self) -> DurabilityHealth:
        """The last known checkpoint/claim health for this session."""
        if self._durable_runtime is None:
            return DurabilityHealth.CLAIM_LOST
        return self._durable_runtime.health_for(self)

    async def attach(
        self,
        message_root: MessageRoot,
        message_destination: MessageDestination,
        *,
        recipe: str,
        actor_id: int | None = None,
        parent: MessageRoot | None = None,
    ) -> Opened[DurableSession] | Rejected | Abandoned | NotDurable:
        """Deliver and durably attach one child mount to this session graph."""
        runtime = self._durable_runtime
        if runtime is None:
            return Rejected((self.snapshot,), RejectionReason.SESSION_FINISHED)
        return await runtime._attach(
            self, message_root, message_destination, recipe=recipe, actor_id=actor_id, parent=parent
        )

    async def join(
        self,
        user_id: int,
        *,
        when: Callable[[frozenset[int]], bool] | None = None,
        expect: frozenset[int] | None = None,
    ) -> MembershipResult:
        """Admit `user_id` and checkpoint the new membership."""
        return await self._durably(await super().join(user_id, when=when, expect=expect))

    async def leave(self, user_id: int, *, expect: frozenset[int] | None = None) -> MembershipResult:
        """Remove `user_id` and checkpoint the new membership."""
        return await self._durably(await super().leave(user_id, expect=expect))

    async def _durably(self, result: MembershipResult) -> MembershipResult:
        """Persist a committed membership change, outside the session lifecycle lock.

        Held inside it, a checkpoint that loses the claim would re-enter `finish` and
        deadlock on the lock the operation still owns — so this follows `_attach`, which
        mutates under the lock and persists after releasing it. A failed checkpoint leaves
        the record dirty at `CHECKPOINT_PENDING` for the maintenance sweep to retry; a
        caller needing read-your-write durability awaits `DurableSessionRuntime.flush`.
        """
        runtime = self._durable_runtime
        if result.committed and runtime is not None:
            await runtime._checkpoint_membership(self)
        return result


type DurableOpenResult = Opened[DurableSession] | Rejected | Abandoned | NotDurable


class DurableSessionRuntime:
    """Coordinate fenced admission, whole-session checkpoints, and frontend recovery."""

    def __init__(
        self,
        *,
        sessions: SessionManager,
        components: ComponentRegistry,
        store: DurableSessionStore,
        frontend: DurableFrontend,
        owner: str | None = None,
        lease_seconds: float = 30.0,
        maintenance_interval: float | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if lease_seconds <= 0:
            message = "durable session lease must be positive"
            raise ValueError(message)
        interval = lease_seconds / 3 if maintenance_interval is None else maintenance_interval
        if interval <= 0 or interval >= lease_seconds:
            message = "maintenance interval must be positive and shorter than the lease"
            raise ValueError(message)
        self.sessions = sessions
        self.components = components
        self.store = store
        self.frontend = frontend
        self.owner = secrets.token_urlsafe(12) if owner is None else owner
        self.lease_seconds = lease_seconds
        self.maintenance_interval = interval
        self.clock = clock
        self._active: dict[str, _ActiveSession] = {}
        self._by_session: dict[DurableSession, str] = {}
        self._dirty: set[str] = set()
        self._wake = asyncio.Event()
        self._maintenance_lock = asyncio.Lock()
        self._running = False
        self._shutting_down = False
        self._last_recovery: RecoveryReport | None = None

    def snapshot(self) -> DurableRuntimeSnapshot:
        """Return durable supervision and checkpoint state for diagnostics."""
        active = tuple(
            (record_id, current.session.health, len(current.session.message_roots))
            for record_id, current in sorted(self._active.items())
        )
        return DurableRuntimeSnapshot(
            self._running,
            self._shutting_down,
            active,
            tuple(sorted(self._dirty)),
            self._last_recovery,
        )

    async def flush(self) -> None:
        """Renew claims and checkpoint every dirty durable session now."""
        self._require_running()
        await self._maintain()

    async def purge(self, record_keys: Sequence[str]) -> tuple[PurgeResult, ...]:
        """Delete explicitly selected inactive records through a fresh fenced claim."""
        self._require_running()
        async with self._maintenance_lock:
            return await self._purge(record_keys)

    async def _purge(self, record_keys: Sequence[str]) -> tuple[PurgeResult, ...]:
        results: list[PurgeResult] = []
        for record_key in dict.fromkeys(record_keys):
            if record_key in self._active:
                results.append(PurgeResult(record_key=record_key, deleted=False, reason="active in this runtime"))
                continue
            token = await self.store.claim(record_key, self.owner, self.lease_seconds)
            if token is None:
                results.append(PurgeResult(record_key=record_key, deleted=False, reason="missing or claimed elsewhere"))
                continue
            deleted = False
            try:
                deleted = await self.store.delete(token)
                results.append(PurgeResult(record_key, deleted, "deleted" if deleted else "claim expired"))
            finally:
                if not deleted:
                    await self.store.release(token)
        return tuple(results)

    async def open(
        self,
        message_root: MessageRoot,
        message_destination: MessageDestination,
        *,
        recipe: str,
        key: SessionKey,
        admission: AdmissionSpec = DEFAULT_ADMISSION,
        actor_id: int | None = None,
        expires_at: float | None = None,
        capacity: int | None = None,
        quota: int | None = None,
        domain: str | None = None,
    ) -> DurableOpenResult:
        """Reserve, deliver, persist, and register one durable session atomically."""
        self._require_running()
        if not self.components.has(recipe):
            message = f"durable component {recipe!r} is not registered"
            raise MessageRootStateError(message)
        scope = encode_session_scope(key)
        reservation = await self.store.reserve(scope, self.owner, self.lease_seconds)
        if reservation is None:
            return Rejected((), RejectionReason.ADMISSION_BUSY)
        consumed = False
        not_durable: NotDurable | None = None
        try:
            stored = await self.store.inspect(reservation)
            if stored is None:
                return Rejected((), RejectionReason.ADMISSION_BUSY)
            remote = tuple(_loads_snapshot(record.snapshot_payload, local=False) for record in stored)
            now = datetime.now(UTC)
            snapshot = SessionSnapshot(str(uuid4()), now, key, actor_id, durable=True, local=True)

            async def persist(
                newcomer: Session,
                delivered: Delivered,
                victims: tuple[SessionSnapshot, ...],
            ) -> None:
                nonlocal consumed, not_durable
                promoted = await self.frontend.promote(message_root, delivered.result)
                if isinstance(promoted, NotDurable):
                    not_durable = promoted
                    raise _NotDurableOpen(promoted)
                assert isinstance(promoted, Promoted)
                record = DurableSessionRecord(
                    protocol=DurableSessionCodec.protocol,
                    id=snapshot.id,
                    key=key,
                    actor_id=actor_id,
                    opened_at=snapshot.opened_at.timestamp(),
                    expires_at=expires_at,
                    message_roots=(
                        SessionRootRecord(
                            "root",
                            self.components.capture(message_root, recipe),
                            promoted.address,
                            None,
                            actor_id,
                        ),
                    ),
                    members=newcomer.members,
                    capacity=capacity,
                    quota=newcomer.quota,
                    domain=newcomer.domain,
                )
                # Persist the live session projection; the opening request has no participants yet.
                # Publishing that empty projection could authorize another user's replacement.
                token = await self.store.commit(
                    reservation,
                    key=snapshot.id,
                    snapshot_payload=_dumps_snapshot(newcomer.snapshot),
                    record_payload=DurableSessionCodec.dumps(record),
                    victims=tuple(victim.id for victim in victims if victim.durable),
                    lease_seconds=self.lease_seconds,
                )
                if token is None:
                    message = "durable admission was lost before the first snapshot committed"
                    raise MessageRootStateError(message)
                consumed = True
                assert isinstance(newcomer, DurableSession)
                self._bind(
                    newcomer,
                    token,
                    expires_at=expires_at,
                    message_root_ids={message_root: "root"},
                    recipes={message_root: recipe},
                    addresses={message_root: promoted.address},
                )

            try:
                return await self.sessions._open_coordinated(
                    message_root,
                    message_destination,
                    key=key,
                    admission=admission,
                    actor_id=actor_id,
                    snapshot=snapshot,
                    remote_occupants=remote,
                    before_registration=persist,
                    session_type=DurableSession,
                    capacity=capacity,
                    quota=quota,
                    domain=domain,
                )
            except _NotDurableOpen:
                assert not_durable is not None
                return not_durable
        finally:
            if not consumed:
                await self.store.abandon(reservation)

    async def run(
        self,
        *,
        task_status: TaskStatus[RecoveryReport] = anyio.TASK_STATUS_IGNORED,
    ) -> None:
        """Recover once, report readiness, then supervise claims and checkpoints."""
        if self._running:
            message = "durable session runtime is already running"
            raise RuntimeError(message)
        self._running = True
        self._shutting_down = False
        try:
            report = await self.recover()
            task_status.started(report)
            while True:
                with anyio.move_on_after(self.maintenance_interval):
                    await self._wake.wait()
                self._wake.clear()
                await self._maintain()
        finally:
            self._shutting_down = True
            for active in tuple(self._active.values()):
                try:
                    await self.store.release(active.token)
                except Exception:
                    logger.exception("could not release durable session claim %s", active.token.key)
                active.session._durable_runtime = None
            self._active.clear()
            self._by_session.clear()
            self._dirty.clear()
            self._running = False

    async def recover(self) -> RecoveryReport:
        """Claim and independently reconnect every available durable session record."""
        self._require_running()
        async with self._maintenance_lock:
            return await self._recover()

    async def _recover(self) -> RecoveryReport:
        """Run recovery while the caller owns the maintenance lock."""
        report = _RecoveryReportBuilder()
        for listed in await self.store.list():
            item = _item_from_stored(listed, "")
            token = await self.store.claim(listed.key, self.owner, self.lease_seconds)
            if token is None:
                report.claimed_elsewhere.append(item)
                continue
            message_roots: list[MessageRoot] = []
            try:
                record = DurableSessionCodec.loads(listed.record_payload)
                item = _item_from_record(record, "")
                if record.expires_at is not None and record.expires_at <= self.clock():
                    await self.store.delete(token)
                    report.expired.append(_item_from_record(record, "session expired before recovery"))
                    continue
                snapshot = _loads_snapshot(listed.snapshot_payload, local=True)
                _validate_snapshot_record(snapshot, record)
                by_id: dict[str, MessageRoot] = {}
                states_by_id = {state.id: state for state in record.message_roots}
                for state in record.message_roots:
                    context = RestoreContext(
                        record.id,
                        record.key,
                        record.actor_id,
                        state.actor_id,
                        state.address,
                        record.expires_at,
                        state.parent_id,
                    )
                    restored = self.components.restore(state.state, context)
                    by_id[state.id] = restored
                    message_roots.append(restored)
                remaining = tuple(record.message_roots)
                reconnect = await self.frontend.reconnect(
                    tuple(RecoveredBinding(state.id, by_id[state.id], state.address) for state in remaining)
                )
                if isinstance(reconnect, Missing):
                    root_id = record.message_roots[0].id
                    if root_id in reconnect.record_message_root_ids:
                        await self.store.delete(token)
                        await _finish_roots(message_roots)
                        report.missing.append(_item_from_record(record, "; ".join(reconnect.reasons)))
                        continue
                    pruned_ids = _descendants(states_by_id, set(reconnect.record_message_root_ids))
                    remaining = tuple(state for state in record.message_roots if state.id not in pruned_ids)
                    await _finish_roots(tuple(by_id[message_root_id] for message_root_id in pruned_ids))
                    reconnect = await self.frontend.reconnect(
                        tuple(RecoveredBinding(state.id, by_id[state.id], state.address) for state in remaining)
                    )
                if isinstance(reconnect, Unreachable):
                    await self.store.release(token)
                    await _finish_roots(message_roots)
                    report.unreachable.append(_item_from_record(record, "; ".join(reconnect.reasons)))
                    continue
                _require_reconnected(reconnect)

                root_state = remaining[0]
                root = by_id[root_state.id]
                attachments = tuple(
                    (by_id[state.id], by_id[state.parent_id], state.actor_id)
                    for state in remaining[1:]
                    if state.parent_id is not None
                )
                session = self.sessions._register_recovered(
                    root,
                    key=record.key,
                    actor_id=record.actor_id,
                    snapshot=snapshot,
                    attachments=attachments,
                    session_type=DurableSession,
                    members=record.members,
                    capacity=record.capacity,
                    quota=record.quota,
                    domain=record.domain,
                )
                self._bind(
                    session,
                    token,
                    expires_at=record.expires_at,
                    message_root_ids={by_id[state.id]: state.id for state in remaining},
                    recipes={by_id[state.id]: state.state.component_key for state in remaining},
                    addresses={by_id[state.id]: state.address for state in remaining},
                )
                report.restored.append(_item_from_record(record, "restored"))
                self._request_checkpoint(record.id)
            except MessageRootStateError as error:
                await self.store.release(token)
                await _finish_roots(message_roots)
                report.incompatible.append(_with_reason(item, error))
            except Exception as error:
                await self.store.release(token)
                await _finish_roots(message_roots)
                logger.exception("could not recover durable session %s", listed.key)
                report.failed.append(_with_reason(item, error))
        frozen = report.freeze()
        self._last_recovery = frozen
        return frozen

    def health_for(self, session: DurableSession) -> DurabilityHealth:
        record_id = self._by_session.get(session)
        if record_id is None or (active := self._active.get(record_id)) is None:
            return DurabilityHealth.CLAIM_LOST
        return active.health

    async def _checkpoint_membership(self, session: DurableSession) -> None:
        """Persist a membership change immediately rather than waiting for maintenance."""
        record_id = self._by_session.get(session)
        active = None if record_id is None else self._active.get(record_id)
        if active is None:
            return
        await self._checkpoint(active)

    async def _attach(
        self,
        session: DurableSession,
        message_root: MessageRoot,
        message_destination: MessageDestination,
        *,
        recipe: str,
        actor_id: int | None,
        parent: MessageRoot | None,
    ) -> Opened[DurableSession] | Rejected | Abandoned | NotDurable:
        self._require_running()
        if not self.components.has(recipe):
            message = f"durable component {recipe!r} is not registered"
            raise MessageRootStateError(message)
        async with session._lifecycle_lock:
            if session._closed or session.root.finished:
                return Rejected((session.snapshot,), RejectionReason.SESSION_FINISHED)
            parent = session.root if parent is None else parent
            if parent not in session.message_roots or parent.finished:
                return Rejected((session.snapshot,), RejectionReason.SESSION_FINISHED)
            result = await message_root.send(message_destination)
            if isinstance(result, Abandoned):
                return result
            promoted = await self.frontend.promote(message_root, result.result)
            if isinstance(promoted, NotDurable):
                await message_root.finish()
                return promoted
            active = self._active_for(session)
            message_root_id = str(uuid4())
            session._attach_existing(message_root, parent=parent, actor_id=actor_id)
            active.message_root_ids[message_root] = message_root_id
            active.recipes[message_root] = recipe
            active.addresses[message_root] = promoted.address
            self._observe(active, message_root)
        await self._checkpoint(active)
        return Opened(session)

    def _bind(
        self,
        session: DurableSession,
        token: ClaimToken,
        *,
        expires_at: float | None,
        message_root_ids: dict[MessageRoot, str],
        recipes: dict[MessageRoot, str],
        addresses: dict[MessageRoot, FrontendAddress],
    ) -> None:
        active = _ActiveSession(session, token, expires_at, message_root_ids, recipes, addresses)
        self._active[token.key] = active
        self._by_session[session] = token.key
        session._durable_runtime = self
        for message_root in session.message_roots:
            self._observe(active, message_root)

    def _observe(self, active: _ActiveSession, message_root: MessageRoot) -> None:
        def committed(_: MessageRoot) -> None:
            self._request_checkpoint(active.token.key)

        async def finished(finished_root: MessageRoot) -> None:
            if finished_root is active.session.root:
                await self._finish_record(active.token.key)
                return
            active.message_root_ids.pop(finished_root, None)
            active.recipes.pop(finished_root, None)
            active.addresses.pop(finished_root, None)
            self._request_checkpoint(active.token.key)

        message_root.on_committed(committed)
        message_root.on_finish(finished)

    def _request_checkpoint(self, record_id: str) -> None:
        if record_id not in self._active:
            return
        self._active[record_id].health = DurabilityHealth.CHECKPOINT_PENDING
        self._dirty.add(record_id)
        self._wake.set()

    async def _maintain(self) -> None:
        async with self._maintenance_lock:
            for record_id, active in tuple(self._active.items()):
                if active.expires_at is not None and active.expires_at <= self.clock():
                    await active.session.finish()
                    continue
                try:
                    renewed = await self.store.renew(active.token, self.lease_seconds)
                except Exception:
                    logger.exception("could not renew durable session claim %s", record_id)
                    renewed = False
                if not renewed:
                    await self._lose_claim(record_id)
            for record_id in tuple(sorted(self._dirty)):
                self._dirty.discard(record_id)
                if (active := self._active.get(record_id)) is not None:
                    await self._checkpoint(active)

    async def _checkpoint(self, active: _ActiveSession) -> bool:
        try:
            async with active.writes:
                record = self._capture(active)
                saved = await self.store.save(
                    active.token,
                    _dumps_snapshot(active.session.snapshot),
                    DurableSessionCodec.dumps(record),
                )
            if not saved:
                await self._lose_claim(active.token.key)
                return False
        except Exception:
            active.health = DurabilityHealth.CHECKPOINT_PENDING
            self._dirty.add(active.token.key)
            logger.exception("could not checkpoint durable session %s", active.token.key)
            return False
        active.health = DurabilityHealth.HEALTHY
        return True

    def _capture(self, active: _ActiveSession) -> DurableSessionRecord:
        session = active.session
        message_roots = tuple(
            SessionRootRecord(
                active.message_root_ids[message_root],
                self.components.capture(message_root, active.recipes[message_root]),
                active.addresses[message_root],
                None if (parent := session.parent_of(message_root)) is None else active.message_root_ids[parent],
                session.actor_for(message_root),
            )
            for message_root in session.message_roots
        )
        return DurableSessionRecord(
            DurableSessionCodec.protocol,
            session.id,
            session.key,
            session.snapshot.actor_id,
            session.opened_at.timestamp(),
            active.expires_at,
            message_roots,
            members=session.members,
            capacity=session.capacity,
            quota=session.quota,
            domain=session.domain,
        )

    async def _finish_record(self, record_id: str) -> None:
        active = self._active.pop(record_id, None)
        self._dirty.discard(record_id)
        if active is None:
            return
        self._by_session.pop(active.session, None)
        active.session._durable_runtime = None
        if self._shutting_down:
            await self.store.release(active.token)
            return
        if not await self.store.delete(active.token):
            active.health = DurabilityHealth.CLAIM_LOST

    async def _lose_claim(self, record_id: str) -> None:
        active = self._active.pop(record_id, None)
        self._dirty.discard(record_id)
        if active is None:
            return
        self._by_session.pop(active.session, None)
        active.health = DurabilityHealth.CLAIM_LOST
        active.session._durable_runtime = None
        await active.session.finish()

    def _active_for(self, session: DurableSession) -> _ActiveSession:
        record_id = self._by_session.get(session)
        active = None if record_id is None else self._active.get(record_id)
        if active is None:
            message = "durable session no longer owns its record"
            raise MessageRootStateError(message)
        return active

    def _require_running(self) -> None:
        if not self._running or self._shutting_down:
            message = "durable sessions can open only while their runtime is supervised"
            raise RuntimeError(message)


def _dumps_snapshot(snapshot: SessionSnapshot) -> str:
    if not isinstance(snapshot.key, SessionKey):
        message = "durable session snapshots require a SessionKey"
        raise MessageRootStateError(message)
    raw = {
        "id": snapshot.id,
        "opened_at": snapshot.opened_at.timestamp(),
        "key": encode_session_key(snapshot.key),
        "actor_id": snapshot.actor_id,
        "members": sorted(snapshot.members),
        "attachment_actors": sorted(snapshot.attachment_actors),
        "durable": True,
    }
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _loads_snapshot(payload: str, *, local: bool) -> SessionSnapshot:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as error:
        raise MessageRootStateError(str(error)) from error
    if not isinstance(raw, dict):
        message = "durable session snapshot must be an object"
        raise MessageRootStateError(message)
    try:
        opened_timestamp = _finite_timestamp(raw["opened_at"])
        opened_at = datetime.fromtimestamp(opened_timestamp, UTC)
        key = decode_session_key(raw["key"])
        actor_id = raw.get("actor_id")
        members = _snapshot_ids(raw.get("members"))
        attachment_actors = _snapshot_ids(raw.get("attachment_actors", ()))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        message = "durable session snapshot is malformed"
        raise MessageRootStateError(message) from error
    if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
        message = "durable session snapshot actor_id is malformed"
        raise MessageRootStateError(message)
    record_id = raw.get("id")
    if not isinstance(record_id, str) or not record_id:
        message = "durable session snapshot id is malformed"
        raise MessageRootStateError(message)
    return SessionSnapshot(
        record_id,
        opened_at,
        key,
        actor_id,
        durable=True,
        local=local,
        members=members,
        attachment_actors=attachment_actors,
    )


def _finite_timestamp(value: object) -> float:
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise ValueError
    return timestamp


def _snapshot_ids(value: object) -> frozenset[int]:
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise ValueError
    return frozenset(value)


def _item_from_stored(record: SessionRecord, reason: str) -> RecoveryItem:
    try:
        snapshot = _loads_snapshot(record.snapshot_payload, local=False)
    except MessageRootStateError:
        return RecoveryItem(record.key, None, (), reason)
    return RecoveryItem(record.key, snapshot.key if isinstance(snapshot.key, SessionKey) else None, (), reason)


def _item_from_record(record: DurableSessionRecord, reason: str) -> RecoveryItem:
    return RecoveryItem(
        record.id, record.key, tuple(message_root.address for message_root in record.message_roots), reason
    )


def _with_reason(item: RecoveryItem, error: Exception) -> RecoveryItem:
    reason = f"{type(error).__name__}: {error}".replace("\n", " ")[:240]
    return RecoveryItem(item.record_key, item.session_key, item.addresses, reason)


def _descendants(states: dict[str, SessionRootRecord], roots: set[str]) -> set[str]:
    descendants = set(roots)
    changed = True
    while changed:
        changed = False
        for state in states.values():
            if state.parent_id in descendants and state.id not in descendants:
                descendants.add(state.id)
                changed = True
    return descendants


async def _finish_roots(message_roots: Sequence[MessageRoot]) -> None:
    for message_root in reversed(tuple(message_roots)):
        try:
            await message_root.finish(disable=False)
        except Exception:
            logger.exception("could not tear down an unregistered recovered mount %s", message_root.id)


def _validate_snapshot_record(snapshot: SessionSnapshot, record: DurableSessionRecord) -> None:
    if snapshot.id != record.id or snapshot.key != record.key or snapshot.members != record.members:
        message = "stored session snapshot does not match its durable record"
        raise MessageRootStateError(message)


def _require_reconnected(result: Reconnected | Missing | Unreachable) -> None:
    if not isinstance(result, Reconnected):
        message = "frontend did not reconnect the recoverable session graph"
        raise MessageRootStateError(message)

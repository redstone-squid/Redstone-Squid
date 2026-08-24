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

from squid_layouts.discord.delivery import Abandoned, Delivered, Destination
from squid_layouts.discord.mount import Mount
from squid_layouts.discord.sessions import (
    DEFAULT_SESSION_POLICY,
    Opened,
    Rejected,
    RejectionReason,
    Session,
    SessionKey,
    SessionPolicy,
    SessionRegistry,
    SessionSummary,
)
from squid_stores import ClaimToken, DurableSessionStore, StoredSessionRecord

from . import ComponentRegistry, MountLocator, RestoreContext, SnapshotError
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
    DurableMountState,
    DurableSessionCodec,
    DurableSessionRecord,
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
    """Sanitized outcome for one durable record in a recovery sweep."""

    record_key: str
    session_key: SessionKey | None
    locators: tuple[MountLocator, ...]
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
    """Outcome of one explicitly requested durable-record purge."""

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
    mount_ids: dict[Mount, str]
    recipes: dict[Mount, str]
    locators: dict[Mount, MountLocator]
    health: DurabilityHealth = DurabilityHealth.HEALTHY


class _NotDurableOpen(Exception):
    def __init__(self, outcome: NotDurable) -> None:
        super().__init__(outcome.reason)
        self.outcome = outcome


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
        mount: Mount,
        destination: Destination,
        *,
        recipe: str,
        actor_id: int | None = None,
        parent: Mount | None = None,
    ) -> Opened[DurableSession] | Rejected | Abandoned | NotDurable:
        """Deliver and durably attach one child mount to this session graph."""
        runtime = self._durable_runtime
        if runtime is None:
            return Rejected((self.summary,), RejectionReason.SESSION_FINISHED)
        return await runtime._attach(self, mount, destination, recipe=recipe, actor_id=actor_id, parent=parent)


type DurableOpenResult = Opened[DurableSession] | Rejected | Abandoned | NotDurable


class DurableSessionRuntime:
    """Coordinate fenced admission, whole-session checkpoints, and frontend recovery."""

    def __init__(
        self,
        *,
        sessions: SessionRegistry,
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
            (record_id, current.session.health, len(current.session.mounts))
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
        mount: Mount,
        destination: Destination,
        *,
        recipe: str,
        key: SessionKey,
        policy: SessionPolicy = DEFAULT_SESSION_POLICY,
        actor_id: int | None = None,
        expires_at: float | None = None,
    ) -> DurableOpenResult:
        """Reserve, deliver, persist, and register one durable session atomically."""
        self._require_running()
        if not self.components.has(recipe):
            message = f"durable component {recipe!r} is not registered"
            raise SnapshotError(message)
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
            remote = tuple(_loads_summary(record.summary_payload, local=False) for record in stored)
            now = datetime.now(UTC)
            summary = SessionSummary(str(uuid4()), now, key, actor_id, durable=True, local=True)

            async def persist(
                newcomer: Session,
                delivered: Delivered,
                victims: tuple[SessionSummary, ...],
            ) -> None:
                nonlocal consumed, not_durable
                promoted = await self.frontend.promote(mount, delivered.receipt)
                if isinstance(promoted, NotDurable):
                    not_durable = promoted
                    raise _NotDurableOpen(promoted)
                assert isinstance(promoted, Promoted)
                record = DurableSessionRecord(
                    protocol=DurableSessionCodec.protocol,
                    id=summary.id,
                    key=key,
                    actor_id=actor_id,
                    opened_at=summary.opened_at.timestamp(),
                    expires_at=expires_at,
                    mounts=(
                        DurableMountState(
                            "root",
                            self.components.capture(mount, recipe),
                            promoted.locator,
                            None,
                            actor_id,
                        ),
                    ),
                )
                # The newcomer's own summary, not the pre-session one built above: that one
                # predates the Session and so carries no participants, and nothing forces a
                # checkpoint after opening. A remote process reading the empty projection would
                # let ProtectCrossUserAttachments retire another user's durable session.
                token = await self.store.commit(
                    reservation,
                    key=summary.id,
                    summary_payload=_dumps_summary(newcomer.summary),
                    snapshot_payload=DurableSessionCodec.dumps(record),
                    victims=tuple(victim.id for victim in victims if victim.durable),
                    lease_seconds=self.lease_seconds,
                )
                if token is None:
                    message = "durable admission was lost before the first snapshot committed"
                    raise SnapshotError(message)
                consumed = True
                assert isinstance(newcomer, DurableSession)
                self._bind(
                    newcomer,
                    token,
                    expires_at=expires_at,
                    mount_ids={mount: "root"},
                    recipes={mount: recipe},
                    locators={mount: promoted.locator},
                )

            try:
                return await self.sessions._open_coordinated(
                    mount,
                    destination,
                    key=key,
                    policy=policy,
                    actor_id=actor_id,
                    summary=summary,
                    remote_occupants=remote,
                    before_registration=persist,
                    session_type=DurableSession,
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
        for listed in await self.store.list_records():
            item = _item_from_stored(listed, "")
            token = await self.store.claim(listed.key, self.owner, self.lease_seconds)
            if token is None:
                report.claimed_elsewhere.append(item)
                continue
            mounts: list[Mount] = []
            try:
                record = DurableSessionCodec.loads(listed.snapshot_payload)
                item = _item_from_record(record, "")
                if record.expires_at is not None and record.expires_at <= self.clock():
                    await self.store.delete(token)
                    report.expired.append(_item_from_record(record, "session expired before recovery"))
                    continue
                summary = _loads_summary(listed.summary_payload, local=True)
                _validate_summary_record(summary, record)
                by_id: dict[str, Mount] = {}
                states_by_id = {state.id: state for state in record.mounts}
                for state in record.mounts:
                    context = RestoreContext(
                        record.id,
                        record.key,
                        record.actor_id,
                        state.actor_id,
                        state.locator,
                        record.expires_at,
                        state.parent_id,
                    )
                    restored = self.components.restore(state.snapshot, context)
                    by_id[state.id] = restored
                    mounts.append(restored)
                remaining = tuple(record.mounts)
                reconnect = await self.frontend.reconnect(
                    tuple(RecoveredBinding(state.id, by_id[state.id], state.locator) for state in remaining)
                )
                if isinstance(reconnect, Missing):
                    root_id = record.mounts[0].id
                    if root_id in reconnect.record_mount_ids:
                        await self.store.delete(token)
                        await _finish_mounts(mounts)
                        report.missing.append(_item_from_record(record, "; ".join(reconnect.reasons)))
                        continue
                    pruned_ids = _descendants(states_by_id, set(reconnect.record_mount_ids))
                    remaining = tuple(state for state in record.mounts if state.id not in pruned_ids)
                    await _finish_mounts(tuple(by_id[mount_id] for mount_id in pruned_ids))
                    reconnect = await self.frontend.reconnect(
                        tuple(RecoveredBinding(state.id, by_id[state.id], state.locator) for state in remaining)
                    )
                if isinstance(reconnect, Unreachable):
                    await self.store.release(token)
                    await _finish_mounts(mounts)
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
                    summary=summary,
                    attachments=attachments,
                    session_type=DurableSession,
                )
                self._bind(
                    session,
                    token,
                    expires_at=record.expires_at,
                    mount_ids={by_id[state.id]: state.id for state in remaining},
                    recipes={by_id[state.id]: state.snapshot.component_key for state in remaining},
                    locators={by_id[state.id]: state.locator for state in remaining},
                )
                report.restored.append(_item_from_record(record, "restored"))
                self._request_checkpoint(record.id)
            except SnapshotError as error:
                await self.store.release(token)
                await _finish_mounts(mounts)
                report.incompatible.append(_with_reason(item, error))
            except Exception as error:
                await self.store.release(token)
                await _finish_mounts(mounts)
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

    async def _attach(
        self,
        session: DurableSession,
        mount: Mount,
        destination: Destination,
        *,
        recipe: str,
        actor_id: int | None,
        parent: Mount | None,
    ) -> Opened[DurableSession] | Rejected | Abandoned | NotDurable:
        self._require_running()
        if not self.components.has(recipe):
            message = f"durable component {recipe!r} is not registered"
            raise SnapshotError(message)
        async with session._lifecycle_lock:
            if session._closed or session.root.finished:
                return Rejected((session.summary,), RejectionReason.SESSION_FINISHED)
            parent = session.root if parent is None else parent
            if parent not in session.mounts or parent.finished:
                return Rejected((session.summary,), RejectionReason.SESSION_FINISHED)
            result = await mount.send(destination)
            if isinstance(result, Abandoned):
                return result
            promoted = await self.frontend.promote(mount, result.receipt)
            if isinstance(promoted, NotDurable):
                await mount.finish()
                return promoted
            active = self._active_for(session)
            mount_id = str(uuid4())
            session._attach_existing(mount, parent=parent, actor_id=actor_id)
            active.mount_ids[mount] = mount_id
            active.recipes[mount] = recipe
            active.locators[mount] = promoted.locator
            self._observe(active, mount)
        await self._checkpoint(active)
        return Opened(session)

    def _bind(
        self,
        session: DurableSession,
        token: ClaimToken,
        *,
        expires_at: float | None,
        mount_ids: dict[Mount, str],
        recipes: dict[Mount, str],
        locators: dict[Mount, MountLocator],
    ) -> None:
        active = _ActiveSession(session, token, expires_at, mount_ids, recipes, locators)
        self._active[token.key] = active
        self._by_session[session] = token.key
        session._durable_runtime = self
        for mount in session.mounts:
            self._observe(active, mount)

    def _observe(self, active: _ActiveSession, mount: Mount) -> None:
        def committed(_: Mount) -> None:
            self._request_checkpoint(active.token.key)

        async def finished(finished_mount: Mount) -> None:
            if finished_mount is active.session.root:
                await self._finish_record(active.token.key)
                return
            active.mount_ids.pop(finished_mount, None)
            active.recipes.pop(finished_mount, None)
            active.locators.pop(finished_mount, None)
            self._request_checkpoint(active.token.key)

        mount.on_committed(committed)
        mount.on_finish(finished)

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
            record = self._capture(active)
            saved = await self.store.save(
                active.token,
                _dumps_summary(active.session.summary),
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
        mounts = tuple(
            DurableMountState(
                active.mount_ids[mount],
                self.components.capture(mount, active.recipes[mount]),
                active.locators[mount],
                None if (parent := session.parent_of(mount)) is None else active.mount_ids[parent],
                session.actor_for(mount),
            )
            for mount in session.mounts
        )
        return DurableSessionRecord(
            DurableSessionCodec.protocol,
            session.id,
            session.key,
            session.summary.actor_id,
            session.opened_at.timestamp(),
            active.expires_at,
            mounts,
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
            raise SnapshotError(message)
        return active

    def _require_running(self) -> None:
        if not self._running or self._shutting_down:
            message = "durable sessions can open only while their runtime is supervised"
            raise RuntimeError(message)


def _dumps_summary(summary: SessionSummary) -> str:
    if not isinstance(summary.key, SessionKey):
        message = "durable session summaries require a SessionKey"
        raise SnapshotError(message)
    raw = {
        "id": summary.id,
        "opened_at": summary.opened_at.timestamp(),
        "key": encode_session_key(summary.key),
        "actor_id": summary.actor_id,
        "participants": sorted(summary.participants),
        "attachment_actors": sorted(summary.attachment_actors),
        "durable": True,
    }
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _loads_summary(payload: str, *, local: bool) -> SessionSummary:
    try:
        raw = json.loads(payload)
    except json.JSONDecodeError as error:
        raise SnapshotError(str(error)) from error
    if not isinstance(raw, dict):
        message = "durable session summary must be an object"
        raise SnapshotError(message)
    try:
        opened_timestamp = _finite_timestamp(raw["opened_at"])
        opened_at = datetime.fromtimestamp(opened_timestamp, UTC)
        key = decode_session_key(raw["key"])
        actor_id = raw.get("actor_id")
        participants = _summary_ids(raw.get("participants", ()))
        attachment_actors = _summary_ids(raw.get("attachment_actors", ()))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        message = "durable session summary is malformed"
        raise SnapshotError(message) from error
    if actor_id is not None and (not isinstance(actor_id, int) or isinstance(actor_id, bool)):
        message = "durable session summary actor_id is malformed"
        raise SnapshotError(message)
    record_id = raw.get("id")
    if not isinstance(record_id, str) or not record_id:
        message = "durable session summary id is malformed"
        raise SnapshotError(message)
    return SessionSummary(
        record_id,
        opened_at,
        key,
        actor_id,
        durable=True,
        local=local,
        participants=participants,
        attachment_actors=attachment_actors,
    )


def _finite_timestamp(value: object) -> float:
    timestamp = float(value)
    if not math.isfinite(timestamp):
        raise ValueError
    return timestamp


def _summary_ids(value: object) -> frozenset[int]:
    if not isinstance(value, list | tuple) or not all(
        isinstance(item, int) and not isinstance(item, bool) for item in value
    ):
        raise ValueError
    return frozenset(value)


def _item_from_stored(record: StoredSessionRecord, reason: str) -> RecoveryItem:
    try:
        summary = _loads_summary(record.summary_payload, local=False)
    except SnapshotError:
        return RecoveryItem(record.key, None, (), reason)
    return RecoveryItem(record.key, summary.key if isinstance(summary.key, SessionKey) else None, (), reason)


def _item_from_record(record: DurableSessionRecord, reason: str) -> RecoveryItem:
    return RecoveryItem(record.id, record.key, tuple(mount.locator for mount in record.mounts), reason)


def _with_reason(item: RecoveryItem, error: Exception) -> RecoveryItem:
    reason = f"{type(error).__name__}: {error}".replace("\n", " ")[:240]
    return RecoveryItem(item.record_key, item.session_key, item.locators, reason)


def _descendants(states: dict[str, DurableMountState], roots: set[str]) -> set[str]:
    descendants = set(roots)
    changed = True
    while changed:
        changed = False
        for state in states.values():
            if state.parent_id in descendants and state.id not in descendants:
                descendants.add(state.id)
                changed = True
    return descendants


async def _finish_mounts(mounts: Sequence[Mount]) -> None:
    for mount in reversed(tuple(mounts)):
        try:
            await mount.finish(disable=False)
        except Exception:
            logger.exception("could not tear down an unregistered recovered mount %s", mount.id)


def _validate_summary_record(summary: SessionSummary, record: DurableSessionRecord) -> None:
    if summary.id != record.id or summary.key != record.key:
        message = "stored session summary does not match its snapshot record"
        raise SnapshotError(message)


def _require_reconnected(result: Reconnected | Missing | Unreachable) -> None:
    if not isinstance(result, Reconnected):
        message = "frontend did not reconnect the recoverable session graph"
        raise SnapshotError(message)

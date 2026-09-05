"""Operational inspection and controls for live Discord layout runtimes."""

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import anyio

from squid_ui.errors import SquidUiError
from squid_ui.profiling import NoOpProfiler, Profiler, RuntimeSnapshot
from squid_ui.runtime.histories import HistorySnapshot, inspect_histories
from squid_ui.runtime.topics import Address, BusSnapshot, CellAddress, Topic, TopicBus
from squid_ui_discord.live import find, message_roots
from squid_ui_discord.message_root_contracts import MessageRootSnapshot
from squid_ui_discord.message_root_scheduler import MessageRootScheduler, MessageRootSchedulerSnapshot
from squid_ui_discord.sessions import Session, SessionManager

if TYPE_CHECKING:
    # Annotations only. Kept out of the runtime import graph so `import squid_ui_discord` does
    # not reach `squid_storage`, which arrives with the `durable` extra rather than the base
    # install. Deferred evaluation makes this free on 3.14.
    from squid_ui_discord.durability.runtime import (
        DurableRuntimeSnapshot,
        DurableSessionRuntime,
        PurgeResult,
    )


class DevToolsAction(StrEnum):
    """One `DevToolsRuntime` action, as `DevToolsPolicy` enables and gates it."""

    REFRESH_MOUNT = "refresh_mount"
    CLOSE_SESSION = "close_session"
    WAIT_IDLE = "wait_idle"
    FLUSH_PERSISTENCE = "flush_persistence"
    RECOVER_PERSISTENCE = "recover_persistence"
    CLEAR_PROFILE = "clear_profile"
    PURGE_PERSISTENCE = "purge_persistence"


_DEFAULT_ACTIONS = frozenset(
    {
        DevToolsAction.REFRESH_MOUNT,
        DevToolsAction.CLOSE_SESSION,
        DevToolsAction.WAIT_IDLE,
        DevToolsAction.FLUSH_PERSISTENCE,
        DevToolsAction.CLEAR_PROFILE,
    }
)
_DEFAULT_CONFIRMATIONS = frozenset(
    {
        DevToolsAction.CLOSE_SESSION,
        DevToolsAction.RECOVER_PERSISTENCE,
        DevToolsAction.PURGE_PERSISTENCE,
    }
)


@dataclass(frozen=True, slots=True)
class DevToolsPolicy:
    """Which actions a `DevToolsRuntime` runs, and which need `confirmed=True`.

    By default everything but `RECOVER_PERSISTENCE` and `PURGE_PERSISTENCE` is enabled, and
    closing a session, recovering and purging need confirmation.
    """

    enabled: frozenset[DevToolsAction] = _DEFAULT_ACTIONS
    confirmations: frozenset[DevToolsAction] = _DEFAULT_CONFIRMATIONS

    def permits(self, action: DevToolsAction) -> bool:
        return action in self.enabled

    def requires_confirmation(self, action: DevToolsAction) -> bool:
        return action in self.confirmations


@dataclass(frozen=True, slots=True)
class SessionInspection:
    """One live `Session`, flattened to JSON-safe values for the dashboard and export."""

    id: str
    key: str
    """`repr` of the session key, which need not be a string."""
    actor_id: int | None
    durable: bool
    local: bool
    opened_at: datetime
    participants: tuple[int, ...]
    message_roots: tuple[str, ...]
    """Ids of the message roots the session holds."""
    members: tuple[int, ...] = ()
    capacity: int | None = None
    """`None` when membership is unbounded; `remaining_capacity` is `None` with it."""
    remaining_capacity: int | None = None
    quota: int | None = None
    domain: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRootInspection:
    """Expensive detail for one live message root, beyond its cheap snapshot."""

    snapshot: MessageRootSnapshot
    middleware: tuple[str, ...]
    observed: tuple[str, ...]
    """Addresses the last render read, as `Topic` text or `module.Class.field` for a cell."""
    followed: tuple[str, ...]
    """Addresses the root is subscribed to, in the same form as `observed`."""
    histories: tuple[HistorySnapshot, ...]
    """Every history stack declared by any component in the tree, without invoking inverses."""


@dataclass(frozen=True, slots=True)
class DurableRecordInspection:
    """Sizes and identity of one persisted durable record, read without claiming it."""

    key: str
    scope: str
    snapshot_bytes: int
    record_bytes: int


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    """Everything `DevToolsRuntime.snapshot()` reads; `!dev ui export` serializes it whole.

    `scheduler`, `topics` and `durable` are `None` when that runtime is not configured or,
    for `topics`, when the bus is not a `SnapshotableBus`.
    """

    sessions: tuple[SessionInspection, ...]
    message_roots: tuple[MessageRootSnapshot, ...]
    scheduler: MessageRootSchedulerSnapshot | None
    topics: BusSnapshot | None
    profiler: RuntimeSnapshot
    durable: DurableRuntimeSnapshot | None


@dataclass(frozen=True, slots=True)
class DevToolsOperation:
    """What `DevToolsRuntime.audit` receives: one per action run, refused, or missing confirmation."""

    action: DevToolsAction
    target: str | None
    """The message root id, session id, or comma-joined record keys; `None` for runtime-wide actions."""
    success: bool
    detail: str


@dataclass(frozen=True, slots=True)
class OperationResult:
    """A completed action; `detail` is the sentence the command shows the caller."""

    action: DevToolsAction
    target: str | None
    detail: str


class DevToolsError(SquidUiError, RuntimeError):
    """Base of every refusal a `DevToolsRuntime` action raises."""


class ActionDisabled(DevToolsError):
    """`DevToolsPolicy.enabled` does not contain the action."""


class ConfirmationRequired(DevToolsError):
    """The action is in `DevToolsPolicy.confirmations` and the caller passed `confirmed=False`."""


class TargetNotFound(DevToolsError):
    """No live message root or session has the given id."""


class RuntimeUnavailable(DevToolsError):
    """The action needs `sessions` or `durable`, and the runtime was built without it."""


AuditHook = Callable[[DevToolsOperation], None]


@runtime_checkable
class SnapshotableBus(Protocol):
    """A bus that can report its own queue depth.

    `TopicBus` does not require this -- a bus that only delivers is a legal bus -- so the
    capability is a separate shape rather than an optional method, and asking for it is an
    `isinstance` rather than a `getattr` and a cast at each call site.
    """

    def snapshot(self) -> BusSnapshot:
        """Queue depth, in-flight count and per-topic subscriber counts at this instant."""
        ...


def _bus_snapshot(bus: TopicBus) -> BusSnapshot | None:
    """This bus's queue depth, or None when it does not report one."""
    return bus.snapshot() if isinstance(bus, SnapshotableBus) else None


class DevToolsRuntime:
    """Diagnostics and policy-gated actions over one process's message roots and sessions.

    `bus` defaults to `scheduler.bus`; `profiler` to the scheduler's, then the bus's, then a
    `NoOpProfiler`. Every action first consults `policy` and raises `ActionDisabled` when it
    is not enabled or `ConfirmationRequired` when it needs `confirmed=True`; both refusals
    reach `audit` before raising. Actions on `durable` raise `RuntimeUnavailable` when none
    was supplied.
    """

    def __init__(
        self,
        *,
        sessions: SessionManager | None = None,
        scheduler: MessageRootScheduler | None = None,
        bus: TopicBus | None = None,
        profiler: Profiler | None = None,
        durable: DurableSessionRuntime | None = None,
        policy: DevToolsPolicy | None = None,
        audit: AuditHook | None = None,
    ) -> None:
        self.sessions = sessions
        self.scheduler = scheduler
        self.bus = bus if bus is not None else scheduler.bus if scheduler is not None else None
        self.profiler = (
            profiler
            if profiler is not None
            else scheduler.profiler
            if scheduler is not None
            else getattr(self.bus, "profiler", NoOpProfiler())
        )
        self.durable = durable
        self.policy = DevToolsPolicy() if policy is None else policy
        self.audit = audit

    def snapshot(self) -> OperationalSnapshot:
        """Return the current bounded process-wide diagnostic state."""
        sessions = (
            () if self.sessions is None else tuple(_session_inspection(session) for session in self.sessions.active())
        )
        topics = None if self.bus is None else _bus_snapshot(self.bus)
        return OperationalSnapshot(
            sessions,
            tuple(message_root.snapshot() for message_root in message_roots()),
            None if self.scheduler is None else self.scheduler.snapshot(),
            topics,
            self.profiler.snapshot(),
            None if self.durable is None else self.durable.snapshot(),
        )

    async def records(self) -> tuple[DurableRecordInspection, ...]:
        """List persisted durable records without claiming or changing them. Raises `RuntimeUnavailable`."""
        runtime = self._require_durable()
        return tuple(
            DurableRecordInspection(record.key, record.scope, len(record.snapshot_payload), len(record.record_payload))
            for record in await runtime.store.list()
        )

    def inspect_root(self, message_root_id: str) -> MessageRootInspection:
        """Inspect a live message root and its component-owned history stacks.

        Not policy-gated. Raises `TargetNotFound` when no live root has the id.
        """
        message_root = find(message_root_id)
        if message_root is None:
            message = f"no live message root {message_root_id!r}"
            raise TargetNotFound(message)
        histories = tuple(
            history
            for component in message_root.runtime.components.values()
            for history in inspect_histories(component)
        )
        return MessageRootInspection(
            message_root.snapshot(),
            message_root.middleware,
            tuple(_address_text(address) for address in message_root.observed),
            tuple(_address_text(address) for address in message_root.followed),
            histories,
        )

    async def refresh_root(self, message_root_id: str) -> OperationResult:
        """Render and deliver one message root immediately. Raises `TargetNotFound`."""
        self._authorize(DevToolsAction.REFRESH_MOUNT, message_root_id, confirmed=True)
        message_root = find(message_root_id)
        if message_root is None:
            message = f"no live message root {message_root_id!r}"
            raise TargetNotFound(message)
        await message_root.refresh()
        return self._success(DevToolsAction.REFRESH_MOUNT, message_root_id, "message root refreshed")

    async def close_session(self, session_id: str, *, confirmed: bool = False) -> OperationResult:
        """Finish one logical session through `sessions`.

        Raises `RuntimeUnavailable` without a session registry and `TargetNotFound` when no
        live session has the id.
        """
        self._authorize(DevToolsAction.CLOSE_SESSION, session_id, confirmed=confirmed)
        if self.sessions is None:
            message = "no session registry is configured"
            raise RuntimeUnavailable(message)
        session = self.sessions.find(session_id)
        if session is None:
            message = f"no live session {session_id!r}"
            raise TargetNotFound(message)
        await session.finish()
        return self._success(DevToolsAction.CLOSE_SESSION, session_id, "session closed")

    async def wait_idle(self) -> OperationResult:
        """Return once the bus and scheduler both report empty queues across two consecutive checks.

        The second check runs after one loop tick, so work the first drain enqueued is seen.
        Never times out.
        """
        self._authorize(DevToolsAction.WAIT_IDLE, None, confirmed=True)
        while True:
            await self._wait_bus_idle()
            if self.scheduler is not None:
                await self.scheduler.wait_idle()

            if self._queues_idle():
                await anyio.sleep(0)
                if self._queues_idle():
                    break
        return self._success(DevToolsAction.WAIT_IDLE, None, "runtime is idle")

    async def _wait_bus_idle(self) -> None:
        """Drain an asynchronous bus when the configured implementation owns a queue."""
        if self.bus is None:
            return
        wait_idle = getattr(self.bus, "wait_idle", None)
        if not callable(wait_idle):
            return
        result = wait_idle()
        if inspect.isawaitable(result):
            await result

    def _queues_idle(self) -> bool:
        """Return whether both configured queue owners report no pending work."""
        if self.bus is not None:
            snapshot = _bus_snapshot(self.bus)
            if snapshot is not None and (snapshot.queued or snapshot.in_flight):
                return False
        if self.scheduler is not None:
            snapshot = self.scheduler.snapshot()
            if snapshot.queued or snapshot.in_flight or snapshot.redeliver:
                return False
        return True

    async def flush_persistence(self) -> OperationResult:
        """Checkpoint dirty durable sessions through `durable.flush()`. Raises `RuntimeUnavailable`."""
        self._authorize(DevToolsAction.FLUSH_PERSISTENCE, None, confirmed=True)
        runtime = self._require_durable()
        await runtime.flush()
        return self._success(DevToolsAction.FLUSH_PERSISTENCE, None, "durable checkpoints flushed")

    async def recover_persistence(self, *, confirmed: bool = False) -> OperationResult:
        """Run `durable.recover()`; the result counts restored sessions. Raises `RuntimeUnavailable`."""
        self._authorize(DevToolsAction.RECOVER_PERSISTENCE, None, confirmed=confirmed)
        runtime = self._require_durable()
        report = await runtime.recover()
        restored = len(report.restored)
        return self._success(DevToolsAction.RECOVER_PERSISTENCE, None, f"recovery completed; restored={restored}")

    def clear_profile(self) -> OperationResult:
        """`profiler.clear()`: drop retained traces, aggregates and health counters."""
        self._authorize(DevToolsAction.CLEAR_PROFILE, None, confirmed=True)
        self.profiler.clear()
        return self._success(DevToolsAction.CLEAR_PROFILE, None, "profiler diagnostics cleared")

    async def purge_persistence(
        self, record_keys: Sequence[str], *, confirmed: bool = False
    ) -> tuple[PurgeResult, ...]:
        """Delete the named durable records through `durable.purge()`.

        A key that is active, missing, or claimed elsewhere comes back undeleted with its
        reason rather than raising; the audit entry succeeds only when every key was deleted.
        Raises `RuntimeUnavailable`.
        """
        target = ",".join(record_keys)
        self._authorize(DevToolsAction.PURGE_PERSISTENCE, target, confirmed=confirmed)
        runtime = self._require_durable()
        results = await runtime.purge(record_keys)
        self._audit(
            DevToolsOperation(
                action=DevToolsAction.PURGE_PERSISTENCE,
                target=target,
                success=all(result.deleted for result in results),
                detail="purge completed",
            )
        )
        return results

    def _require_durable(self) -> DurableSessionRuntime:
        if self.durable is None:
            message = "no durable session runtime is configured"
            raise RuntimeUnavailable(message)
        return self.durable

    def _authorize(self, action: DevToolsAction, target: str | None, *, confirmed: bool) -> None:
        if not self.policy.permits(action):
            self._audit(DevToolsOperation(action=action, target=target, success=False, detail="action disabled"))
            message = f"devtools action {action.value!r} is disabled"
            raise ActionDisabled(message)
        if self.policy.requires_confirmation(action) and not confirmed:
            self._audit(DevToolsOperation(action=action, target=target, success=False, detail="confirmation required"))
            message = f"confirm devtools action {action.value!r} for {target or 'runtime'}"
            raise ConfirmationRequired(message)

    def _success(self, action: DevToolsAction, target: str | None, detail: str) -> OperationResult:
        self._audit(DevToolsOperation(action=action, target=target, success=True, detail=detail))
        return OperationResult(action, target, detail)

    def _audit(self, operation: DevToolsOperation) -> None:
        if self.audit is not None:
            self.audit(operation)


def _session_inspection(session: Session) -> SessionInspection:
    snapshot = session.snapshot
    return SessionInspection(
        session.id,
        repr(snapshot.key),
        snapshot.actor_id,
        snapshot.durable,
        snapshot.local,
        snapshot.opened_at,
        tuple(sorted(snapshot.participants)),
        tuple(message_root.id for message_root in session.message_roots),
        tuple(sorted(snapshot.members)),
        snapshot.capacity,
        snapshot.remaining_capacity,
        session.quota,
        session.domain,
    )


def _address_text(address: Address) -> str:
    if isinstance(address, Topic):
        return str(address)
    if isinstance(address, CellAddress):
        owner = type(address.owner)
        return f"{owner.__module__}.{owner.__qualname__}.{address.name}"
    return repr(address)


__all__ = [
    "ActionDisabled",
    "ConfirmationRequired",
    "DevToolsAction",
    "DevToolsError",
    "DevToolsOperation",
    "DevToolsPolicy",
    "DevToolsRuntime",
    "DurableRecordInspection",
    "MessageRootInspection",
    "OperationResult",
    "OperationalSnapshot",
    "RuntimeUnavailable",
    "SessionInspection",
    "TargetNotFound",
]

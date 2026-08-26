"""Operational inspection and controls for live Discord layout runtimes."""

import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, cast

import anyio

from squid_ui.profiling import NoOpProfiler, Profiler, RuntimeSnapshot
from squid_ui.runtime.histories import HistorySnapshot, inspect_histories
from squid_ui.runtime.topics import Address, BusSnapshot, CellAddress, Topic, TopicBus
from squid_ui_discord.live import find, message_roots
from squid_ui_discord.message_root import MessageRootSnapshot
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
    """An operation exposed by the development control plane."""

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
    """Capabilities and confirmation rules for operational actions."""

    enabled: frozenset[DevToolsAction] = _DEFAULT_ACTIONS
    confirmations: frozenset[DevToolsAction] = _DEFAULT_CONFIRMATIONS

    def permits(self, action: DevToolsAction) -> bool:
        """Whether ``action`` is enabled for this runtime."""
        return action in self.enabled

    def requires_confirmation(self, action: DevToolsAction) -> bool:
        """Whether the caller must explicitly confirm ``action``."""
        return action in self.confirmations


@dataclass(frozen=True, slots=True)
class SessionInspection:
    """A stable, read-only description of one logical session."""

    id: str
    key: str
    actor_id: int | None
    durable: bool
    local: bool
    opened_at: datetime
    participants: tuple[int, ...]
    message_roots: tuple[str, ...]
    members: tuple[int, ...] = ()
    capacity: int | None = None
    remaining_capacity: int | None = None
    quota: int | None = None
    domain: str | None = None


@dataclass(frozen=True, slots=True)
class MessageRootInspection:
    """Expensive detail for one live mount, beyond its cheap snapshot."""

    snapshot: MessageRootSnapshot
    middleware: tuple[str, ...]
    observed: tuple[str, ...]
    followed: tuple[str, ...]
    histories: tuple[HistorySnapshot, ...]


@dataclass(frozen=True, slots=True)
class DurableRecordInspection:
    """Metadata for one persisted durable session record."""

    key: str
    scope: str
    snapshot_bytes: int
    record_bytes: int


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    """The bounded process-wide view rendered by the devtools dashboard."""

    sessions: tuple[SessionInspection, ...]
    message_roots: tuple[MessageRootSnapshot, ...]
    scheduler: MessageRootSchedulerSnapshot | None
    topics: BusSnapshot | None
    profiler: RuntimeSnapshot
    durable: DurableRuntimeSnapshot | None


@dataclass(frozen=True, slots=True)
class DevToolsOperation:
    """The result sent to an optional operational audit hook."""

    action: DevToolsAction
    target: str | None
    success: bool
    detail: str


@dataclass(frozen=True, slots=True)
class OperationResult:
    """A user-facing result for one operational action."""

    action: DevToolsAction
    target: str | None
    detail: str


class DevToolsError(RuntimeError):
    """Base error for refused or unavailable operational actions."""


class ActionDisabled(DevToolsError):
    """The configured policy does not permit an action."""


class ConfirmationRequired(DevToolsError):
    """The caller must explicitly confirm an action before it can run."""


class TargetNotFound(DevToolsError):
    """A requested mount, session, or durable record does not exist."""


class RuntimeUnavailable(DevToolsError):
    """An optional runtime required by an action was not supplied."""


AuditHook = Callable[[DevToolsOperation], None]


class DevToolsRuntime:
    """Coordinate diagnostics and bounded operational actions for one process."""

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
        snapshot_topics = (
            None if self.bus is None else cast(Callable[[], BusSnapshot] | None, getattr(self.bus, "snapshot", None))
        )
        topics = snapshot_topics() if callable(snapshot_topics) else None
        return OperationalSnapshot(
            sessions,
            tuple(message_root.snapshot() for message_root in message_roots()),
            None if self.scheduler is None else self.scheduler.snapshot(),
            topics,
            self.profiler.snapshot(),
            None if self.durable is None else self.durable.snapshot(),
        )

    async def records(self) -> tuple[DurableRecordInspection, ...]:
        """List persisted durable records without claiming or changing them."""
        runtime = self._require_durable()
        return tuple(
            DurableRecordInspection(record.key, record.scope, len(record.snapshot_payload), len(record.record_payload))
            for record in await runtime.store.list()
        )

    def inspect_root(self, message_root_id: str) -> MessageRootInspection:
        """Inspect a live mount and its component-owned history stacks."""
        message_root = find(message_root_id)
        if message_root is None:
            message = f"no live mount {message_root_id!r}"
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
        """Render and deliver one mount immediately."""
        self._authorize(DevToolsAction.REFRESH_MOUNT, message_root_id, confirmed=True)
        message_root = find(message_root_id)
        if message_root is None:
            message = f"no live mount {message_root_id!r}"
            raise TargetNotFound(message)
        await message_root.refresh()
        return self._success(DevToolsAction.REFRESH_MOUNT, message_root_id, "mount refreshed")

    async def close_session(self, session_id: str, *, confirmed: bool = False) -> OperationResult:
        """Finish one logical session through its owning registry."""
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
        """Wait for all configured refresh and topic queues to reach a stable idle point."""
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
            snapshot_topics = cast(Callable[[], BusSnapshot] | None, getattr(self.bus, "snapshot", None))
            snapshot = snapshot_topics() if callable(snapshot_topics) else None
            if snapshot is not None and (snapshot.queued or snapshot.in_flight):
                return False
        if self.scheduler is not None:
            snapshot = self.scheduler.snapshot()
            if snapshot.queued or snapshot.in_flight or snapshot.redeliver:
                return False
        return True

    async def flush_persistence(self) -> OperationResult:
        """Checkpoint pending durable sessions without resetting application state."""
        self._authorize(DevToolsAction.FLUSH_PERSISTENCE, None, confirmed=True)
        runtime = self._require_durable()
        await runtime.flush()
        return self._success(DevToolsAction.FLUSH_PERSISTENCE, None, "durable checkpoints flushed")

    async def recover_persistence(self, *, confirmed: bool = False) -> OperationResult:
        """Run a supervised durable recovery sweep."""
        self._authorize(DevToolsAction.RECOVER_PERSISTENCE, None, confirmed=confirmed)
        runtime = self._require_durable()
        report = await runtime.recover()
        restored = len(report.restored)
        return self._success(DevToolsAction.RECOVER_PERSISTENCE, None, f"recovery completed; restored={restored}")

    def clear_profile(self) -> OperationResult:
        """Clear bounded profiler diagnostics."""
        self._authorize(DevToolsAction.CLEAR_PROFILE, None, confirmed=True)
        self.profiler.clear()
        return self._success(DevToolsAction.CLEAR_PROFILE, None, "profiler diagnostics cleared")

    async def purge_persistence(
        self, record_keys: Sequence[str], *, confirmed: bool = False
    ) -> tuple[PurgeResult, ...]:
        """Purge explicitly named inactive durable records through fenced claims."""
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

"""Operational inspection and controls for live Discord layout runtimes."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from squid_layouts.discord.durability.runtime import (
    DurableRuntimeSnapshot,
    DurableSessionRuntime,
    PurgeResult,
)
from squid_layouts.discord.live import find, mounts
from squid_layouts.discord.mount import MountSnapshot
from squid_layouts.discord.reactor import Reactor, ReactorSnapshot
from squid_layouts.discord.sessions import Session, SessionRegistry
from squid_layouts.profiling import NoOpProfiler, Profiler, RuntimeSnapshot
from squid_layouts.runtime.histories import HistorySnapshot, inspect_histories
from squid_layouts.runtime.topics import Address, BusSnapshot, CellAddress, Topic, TopicBus


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

    def allows(self, action: DevToolsAction) -> bool:
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
    mounts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MountInspection:
    """Expensive detail for one live mount, beyond its cheap snapshot."""

    snapshot: MountSnapshot
    middleware: tuple[str, ...]
    observed: tuple[str, ...]
    followed: tuple[str, ...]
    histories: tuple[HistorySnapshot, ...]


@dataclass(frozen=True, slots=True)
class DurableRecordInspection:
    """Metadata for one persisted durable session record."""

    key: str
    scope: str
    summary_bytes: int
    snapshot_bytes: int


@dataclass(frozen=True, slots=True)
class OperationalSnapshot:
    """The bounded process-wide view rendered by the devtools dashboard."""

    sessions: tuple[SessionInspection, ...]
    mounts: tuple[MountSnapshot, ...]
    reactor: ReactorSnapshot | None
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
        sessions: SessionRegistry | None = None,
        reactor: Reactor | None = None,
        bus: TopicBus | None = None,
        profiler: Profiler | None = None,
        durable: DurableSessionRuntime | None = None,
        policy: DevToolsPolicy | None = None,
        audit: AuditHook | None = None,
    ) -> None:
        self.sessions = sessions
        self.reactor = reactor
        self.bus = bus if bus is not None else reactor.bus if reactor is not None else None
        self.profiler = (
            profiler
            if profiler is not None
            else reactor.profiler
            if reactor is not None
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
        topics = None if self.bus is None else self.bus.snapshot()
        return OperationalSnapshot(
            sessions,
            tuple(mount.snapshot() for mount in mounts()),
            None if self.reactor is None else self.reactor.snapshot(),
            topics,
            self.profiler.snapshot(),
            None if self.durable is None else self.durable.snapshot(),
        )

    async def records(self) -> tuple[DurableRecordInspection, ...]:
        """List persisted durable records without claiming or changing them."""
        runtime = self._require_durable()
        return tuple(
            DurableRecordInspection(record.key, record.scope, len(record.summary_payload), len(record.snapshot_payload))
            for record in await runtime.store.list_records()
        )

    def inspect_mount(self, mount_id: str) -> MountInspection:
        """Inspect a live mount and its component-owned history stacks."""
        mount = find(mount_id)
        if mount is None:
            message = f"no live mount {mount_id!r}"
            raise TargetNotFound(message)
        histories = tuple(
            history for component in mount.runtime.components.values() for history in inspect_histories(component)
        )
        return MountInspection(
            mount.snapshot(),
            mount.middleware,
            tuple(_address_text(address) for address in mount.observed),
            tuple(_address_text(address) for address in mount.followed),
            histories,
        )

    async def refresh_mount(self, mount_id: str) -> OperationResult:
        """Render and deliver one mount immediately."""
        self._authorize(DevToolsAction.REFRESH_MOUNT, mount_id, confirmed=True)
        mount = find(mount_id)
        if mount is None:
            message = f"no live mount {mount_id!r}"
            raise TargetNotFound(message)
        await mount.refresh_now()
        return self._success(DevToolsAction.REFRESH_MOUNT, mount_id, "mount refreshed")

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
        """Wait for all configured refresh and topic queues to settle."""
        self._authorize(DevToolsAction.WAIT_IDLE, None, confirmed=True)
        if self.reactor is not None:
            await self.reactor.wait_idle()
        if self.bus is not None:
            await self.bus.wait_idle()
        return self._success(DevToolsAction.WAIT_IDLE, None, "runtime is idle")

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
        if not self.policy.allows(action):
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
    summary = session.summary
    return SessionInspection(
        session.id,
        repr(summary.key),
        summary.actor_id,
        summary.durable,
        summary.local,
        summary.opened_at,
        tuple(sorted(summary.participants)),
        tuple(mount.id for mount in session.mounts),
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
    "MountInspection",
    "OperationResult",
    "OperationalSnapshot",
    "RuntimeUnavailable",
    "SessionInspection",
    "TargetNotFound",
]

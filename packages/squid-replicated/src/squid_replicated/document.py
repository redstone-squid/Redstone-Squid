"""Reactive replicated documents integrated as Squid action participants."""

import weakref
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from squid_reactive.actions import (
    ActionContext,
    ActionKind,
    ActorRef,
    CausalRef,
    ChangeSummary,
    ConflictDetail,
    ParticipantChange,
)
from squid_reactive.core import (
    ReactiveConflictError,
    TransactionView,
    _Cell,
    action_participant,
    join_action,
    transaction,
)
from squid_replicated.fake import (
    FakeEngine,
    FakeOperation,
    FakeSnapshot,
    FakeVersion,
    PreparedFakeUpdate,
)
from squid_replicated.transport import ReplicatedUpdate

_DEDUP_LIMIT = 10_000
_PENDING_UPDATE_LIMIT = 1_000


class ReplicatedClosedError(RuntimeError):
    """A replicated scope or document has been closed and no longer grants mutation authority."""


@dataclass(frozen=True, slots=True)
class ReplicatedChangeToken:
    """An action-addressable semantic token retained by opted-in history."""

    document: weakref.ReferenceType[ReplicatedDocument]
    operations: tuple[FakeOperation, ...]

    def encode(self) -> bytes:
        """Encode this token for durable history while its document remains available."""
        document = self.document()
        if document is None or document.closed:
            message = "replicated history token no longer has a live document"
            raise ReplicatedClosedError(message)
        return document.engine.encode_token(self.operations)

    @classmethod
    def decode(cls, document: ReplicatedDocument, token: bytes) -> ReplicatedChangeToken:
        """Reload an encoded token against the current instance of its document."""
        document._ensure_open()
        return cls(weakref.ref(document), document.engine.decode_token(token))

    def plan_inverse(self) -> tuple[FakeOperation, ...] | ConflictDetail:
        document = self.document()
        if document is None or document.closed:
            return ConflictDetail("replicated:closed", 0, 0)
        inverse: list[FakeOperation] = []
        for operation in reversed(self.operations):
            if operation.kind == "increment":
                assert isinstance(operation.value, int)
                inverse.append(document.engine.operation("increment", operation.path, -operation.value))
            elif operation.kind == "add":
                inverse.append(
                    document.engine.operation("remove", operation.path, operation.value, (operation.identity,))
                )
            else:
                return ConflictDetail(f"replicated:{operation.path}", 0, 0)
        return tuple(inverse)

    def stage_inverse(self, operations: tuple[FakeOperation, ...]) -> None:
        document = self.document()
        if document is None or document.closed:
            detail = ConflictDetail("replicated:closed", 0, 0)
            raise ReactiveConflictError(detail, "replicated document is no longer available")
        participant = document._participant()
        for operation in operations:
            participant.branch.apply(operation)


class _ReplicationParticipant:
    def __init__(
        self,
        document: ReplicatedDocument,
        *,
        remote: PreparedFakeUpdate | None = None,
        remote_update_id: str | None = None,
    ) -> None:
        self.document = document
        self.branch = document.engine.branch()
        self.remote = remote
        self.remote_update_id = remote_update_id

    def prepare(self, view: TransactionView) -> PreparedFakeUpdate:
        if self.remote is not None:
            return self.remote
        base = self.branch.base
        if self.document.engine.version() != base:
            detail = ConflictDetail(
                self.document.identity, len(base.operations), len(self.document.engine.version().operations)
            )
            raise ReactiveConflictError(detail, f"{self.document.identity} changed before replicated prepare")
        return self.branch.prepare(base)

    def describe_change(self, prepared: PreparedFakeUpdate) -> ParticipantChange | None:
        if not prepared.operations:
            return None
        token = ReplicatedChangeToken(weakref.ref(self.document), prepared.operations)
        return ParticipantChange(self.document.identity, token, ChangeSummary(participants=1))

    def apply(self, prepared: PreparedFakeUpdate) -> None:
        self.document.engine.apply(prepared)
        self.document._version_cell.write(self.document.engine.snapshot())

    def abort(self, prepared: PreparedFakeUpdate | None, cause: BaseException) -> None:
        return None

    def finalize(self, prepared: PreparedFakeUpdate) -> None:
        self.document._notify()
        if self.remote_update_id is None and prepared.operations:
            self.document._publish_update(prepared)


class ReplicatedDocument:
    """One immutable-snapshot replicated document. Calling :meth:`close` ends all access."""

    def __init__(self, document_id: str, engine: FakeEngine) -> None:
        self.document_id = document_id
        self.engine = engine
        self.closed = False
        self._version_cell = _Cell(engine.snapshot(), address=f"replicated:{document_id}")
        self._listeners: set[Any] = set()
        self._update_listeners: set[Callable[[ReplicatedUpdate], None]] = set()
        self._pending_updates: deque[ReplicatedUpdate] = deque(maxlen=_PENDING_UPDATE_LIMIT)
        self._seen_updates: deque[str] = deque(maxlen=_DEDUP_LIMIT)
        self._seen_update_ids: set[str] = set()

    @property
    def identity(self) -> str:
        return f"replicated:{self.document_id}"

    def snapshot(self) -> FakeSnapshot:
        self._ensure_open()
        self._version_cell.read()
        participant = action_participant(self)
        return (
            participant.branch.snapshot()
            if isinstance(participant, _ReplicationParticipant)
            else self.engine.snapshot()
        )

    def counter(self, path: str) -> ReplicatedCounter:
        return ReplicatedCounter(self, path)

    def set(self, path: str) -> ReplicatedSet:
        return ReplicatedSet(self, path)

    def export_since(self, version: FakeVersion | None = None) -> bytes:
        self._ensure_open()
        update = ReplicatedUpdate.create(
            document_id=self.document_id,
            backend_id=self.engine.backend_id,
            source_replica_id=self.engine.replica_id,
            payload=self.engine.export_since(version),
            origin_action_id=None,
        )
        return update.encode()

    def import_update(self, update: bytes) -> None:
        self._ensure_open()
        envelope = ReplicatedUpdate.decode(update)
        if envelope.document_id != self.document_id:
            message = f"replicated update targets {envelope.document_id!r}, not {self.document_id!r}"
            raise ValueError(message)
        if envelope.backend_id != self.engine.backend_id:
            message = f"replicated update uses backend {envelope.backend_id!r}, not {self.engine.backend_id!r}"
            raise ValueError(message)
        update_id = str(envelope.update_id)
        if update_id in self._seen_update_ids:
            return
        prepared = self.engine.prepare_remote(envelope.payload)
        cause = (
            None if envelope.origin_action_id is None else CausalRef("remote_action", str(envelope.origin_action_id))
        )
        context = ActionContext.create(
            f"Import {self.document_id}",
            kind=ActionKind.REMOTE,
            cause=cause,
            root_action_id=envelope.origin_action_id,
            actor=ActorRef("replica", envelope.source_replica_id),
            metadata={"document_id": self.document_id, "update_id": update_id},
        )
        with transaction(action_context=context):
            joined = join_action(
                self,
                lambda: _ReplicationParticipant(self, remote=prepared, remote_update_id=update_id),
            )
            assert joined is not None
        self._remember_update(update_id)

    def subscribe(self, callback: Callable[[FakeSnapshot], None]) -> Callable[[], None]:
        self._listeners.add(callback)

        def unsubscribe() -> None:
            self._listeners.discard(callback)

        return unsubscribe

    def subscribe_updates(self, callback: Callable[[ReplicatedUpdate], None]) -> Callable[[], None]:
        """Receive committed local updates until the returned function or document closes it."""
        self._ensure_open()
        self._update_listeners.add(callback)

        def unsubscribe() -> None:
            self._update_listeners.discard(callback)

        return unsubscribe

    def drain_updates(self) -> tuple[ReplicatedUpdate, ...]:
        """Take committed outbound updates retained for an application-owned transport."""
        self._ensure_open()
        updates = tuple(self._pending_updates)
        self._pending_updates.clear()
        return updates

    def close(self) -> None:
        self.closed = True
        self._listeners.clear()
        self._update_listeners.clear()
        self._pending_updates.clear()
        self._seen_updates.clear()
        self._seen_update_ids.clear()

    def _participant(self) -> _ReplicationParticipant:
        self._ensure_open()
        participant = join_action(self, lambda: _ReplicationParticipant(self))
        if participant is None:
            message = "replicated mutations require a Squid transaction"
            raise RuntimeError(message)
        return participant

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            callback(self.snapshot())

    def _publish_update(self, prepared: PreparedFakeUpdate) -> None:
        from squid_reactive import current_action

        context = current_action()
        update = ReplicatedUpdate.create(
            document_id=self.document_id,
            backend_id=self.engine.backend_id,
            source_replica_id=self.engine.replica_id,
            payload=self.engine.encode_update(prepared.operations),
            origin_action_id=None if context is None else context.action_id,
        )
        self._pending_updates.append(update)
        for callback in tuple(self._update_listeners):
            callback(update)

    def _remember_update(self, update_id: str) -> None:
        if len(self._seen_updates) == self._seen_updates.maxlen:
            expired = self._seen_updates.popleft()
            self._seen_update_ids.discard(expired)
        self._seen_updates.append(update_id)
        self._seen_update_ids.add(update_id)

    def _ensure_open(self) -> None:
        if self.closed:
            message = f"replicated document {self.document_id!r} is closed"
            raise ReplicatedClosedError(message)


@dataclass(frozen=True, slots=True)
class ReplicatedCounter:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> int:
        return self.document.snapshot().counter(self.path)

    def increment(self, amount: int = 1) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.operation("increment", self.path, amount))


@dataclass(frozen=True, slots=True)
class ReplicatedSet:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> frozenset[str]:
        return self.document.snapshot().tagged_set(self.path)

    def add(self, value: str) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.operation("add", self.path, value))

    def discard(self, value: str) -> None:
        participant = self.document._participant()
        tags = self.document.engine.visible_tags(self.path, value, participant.branch.operations)
        participant.branch.apply(self.document.engine.operation("remove", self.path, value, tags))


class ReplicatedScope:
    """Own replicated documents and listeners. Calling :meth:`close` closes every document."""

    def __init__(self, replica_id: str) -> None:
        self.replica_id = replica_id
        self._documents: dict[str, ReplicatedDocument] = {}
        self.closed = False

    def open(self, document_id: str) -> ReplicatedDocument:
        if self.closed:
            message = "replicated scope is closed"
            raise ReplicatedClosedError(message)
        document = self._documents.get(document_id)
        if document is None:
            document = self._documents[document_id] = ReplicatedDocument(document_id, FakeEngine(self.replica_id))
        return document

    def close(self) -> None:
        for document in self._documents.values():
            document.close()
        self._documents.clear()
        self.closed = True

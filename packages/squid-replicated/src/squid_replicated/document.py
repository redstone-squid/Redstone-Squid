"""Reactive replicated documents integrated as Squid action participants."""

import base64
import binascii
import json
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
class PreparedReplicatedInverse:
    """An opaque semantic inverse tied to the backend-history generation that planned it."""

    operations: tuple[FakeOperation, ...]
    token_epoch: int


@dataclass(frozen=True, slots=True)
class ReplicatedChangeToken:
    """An action-addressable semantic token retained by opted-in history."""

    document: weakref.ReferenceType[ReplicatedDocument]
    operations: tuple[FakeOperation, ...]
    token_epoch: int

    def encode(self) -> bytes:
        """Encode this token for durable history while its document remains available."""
        document = self.document()
        if document is None or document.closed:
            message = "replicated history token no longer has a live document"
            raise ReplicatedClosedError(message)
        payload = {
            "backend": document.engine.backend_id,
            "document": document.document_id,
            "payload": base64.b64encode(document.engine.encode_token(self.operations)).decode("ascii"),
            "schema": 1,
            "token_epoch": self.token_epoch,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, document: ReplicatedDocument, token: bytes) -> ReplicatedChangeToken:
        """Reload an encoded token against the current instance of its document."""
        document._ensure_open()
        try:
            payload: Any = json.loads(token)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "replicated history token has an unsupported or corrupt schema"
            raise ValueError(message) from error
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            message = "replicated history token has an unsupported or corrupt schema"
            raise ValueError(message)
        if payload.get("backend") != document.engine.backend_id or payload.get("document") != document.document_id:
            message = "replicated history token targets the wrong backend or document"
            raise ValueError(message)
        encoded = payload.get("payload")
        token_epoch = payload.get("token_epoch")
        if not isinstance(encoded, str) or not isinstance(token_epoch, int):
            message = "replicated history token has an unsupported or corrupt schema"
            raise ValueError(message)  # noqa: TRY004
        try:
            operations = document.engine.decode_token(base64.b64decode(encoded, validate=True))
        except (binascii.Error, TypeError, ValueError) as error:
            message = "replicated history token has corrupt backend data"
            raise ValueError(message) from error
        return cls(weakref.ref(document), operations, token_epoch)

    def plan_inverse(self) -> PreparedReplicatedInverse | ConflictDetail:
        document = self.document()
        if document is None or document.closed:
            return ConflictDetail("replicated:closed", 0, 0)
        if self.token_epoch != document.token_epoch:
            return ConflictDetail(f"replicated:{document.document_id}:expired", self.token_epoch, document.token_epoch)
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
        return PreparedReplicatedInverse(tuple(inverse), self.token_epoch)

    def stage_inverse(self, inverse: PreparedReplicatedInverse) -> None:
        document = self.document()
        if document is None or document.closed:
            detail = ConflictDetail("replicated:closed", 0, 0)
            raise ReactiveConflictError(detail, "replicated document is no longer available")
        if inverse.token_epoch != document.token_epoch:
            detail = ConflictDetail(
                f"replicated:{document.document_id}:expired",
                inverse.token_epoch,
                document.token_epoch,
            )
            raise ReactiveConflictError(detail, "replicated inverse expired before it could be staged")
        participant = document._participant()
        for operation in inverse.operations:
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
        token = ReplicatedChangeToken(weakref.ref(self.document), prepared.operations, self.document.token_epoch)
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
        self._token_epoch = 0

    @property
    def identity(self) -> str:
        return f"replicated:{self.document_id}"

    @property
    def token_epoch(self) -> int:
        """Return the local backend-history generation used by retained inverse tokens."""
        return self._token_epoch

    @property
    def pending_update_count(self) -> int:
        """Return the bounded number of committed envelopes awaiting host transport."""
        return len(self._pending_updates)

    @property
    def subscription_count(self) -> int:
        """Return the snapshot and outbound listener authorities owned by this document."""
        return len(self._listeners) + len(self._update_listeners)

    @property
    def deduplication_count(self) -> int:
        """Return the bounded number of remote update identities retained for deduplication."""
        return len(self._seen_update_ids)

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

    def expire_history_tokens(self) -> None:
        """Expire retained inverse authority before backend compaction discards its metadata."""
        self._ensure_open()
        self._token_epoch += 1

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

    @property
    def active_documents(self) -> tuple[str, ...]:
        """Return the document identities whose authority this scope still owns."""
        return tuple(self._documents)

    def close(self) -> None:
        for document in self._documents.values():
            document.close()
        self._documents.clear()
        self.closed = True

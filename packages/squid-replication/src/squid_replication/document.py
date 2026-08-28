"""Reactive replicated documents integrated as Squid action participants."""

import base64
import binascii
import json
import uuid
import weakref
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from squid_reactivity.actions import (
    ActionContext,
    ActionPurpose,
    ActorRef,
    CausalRef,
    ChangeReport,
    ConflictDetail,
    TransactionContribution,
)
from squid_reactivity.core import (
    ReactiveConflictError,
    TransactionView,
    action_participant,
    enlist,
    transaction,
)
from squid_reactivity.internals import Cell as _Cell
from squid_replication.engine import ReplicationBackend, ReplicationEngine
from squid_replication.model import (
    ReplicatedItem,
    ReplicatedTreeSnapshot,
    ReplicatedValue,
    freeze_value,
)
from squid_replication.transport import ReplicationUpdate

_DEDUP_LIMIT = 10_000
_PENDING_UPDATE_LIMIT = 1_000


class ReplicationError(Exception):
    """Base class for every failure squid-replication raises deliberately.

    Each error keeps a standard exception base alongside this one (`RuntimeError`,
    `ValueError`, or `TypeError`), so catching by standard type keeps working while
    `except ReplicationError` covers the package.
    """


class ReplicaClosedError(ReplicationError, RuntimeError):
    """A replicated scope or document has been closed and no longer grants mutation authority."""


class ReplicationResyncRequiredError(ReplicationError, RuntimeError):
    """The bounded outbound buffer overflowed, so what it still holds is an incomplete history.

    Recover by exporting from the peer's version and acknowledging the resync. Nothing is lost
    permanently -- the operation log can still answer any version -- but the buffer alone can
    no longer carry a peer forward.
    """


class ReplicationCorruptUpdateError(ReplicationError, ValueError):
    """A backend update or durable token failed structural or native decoding."""


class ReplicationBackendIntegrityError(ReplicationError, RuntimeError):
    """A validated backend operation failed at the canonical apply boundary."""


class UnsupportedReplicationContainerError(ReplicationError, TypeError):
    """The explicitly selected backend does not implement a requested container class."""


@dataclass(frozen=True, slots=True)
class PreparedReplicationInverse:
    """An opaque semantic inverse tied to the backend-history generation that planned it."""

    payload: object
    token_epoch: int


@dataclass(frozen=True, slots=True)
class ReplicationChangeToken:
    """An action-addressable semantic token retained by opted-in history."""

    document: weakref.ReferenceType[ReplicatedDocument]
    backend_token: object
    token_epoch: int

    def encode(self) -> bytes:
        """Encode this token for durable history while its document remains available."""
        document = self.document()
        if document is None or document.closed:
            message = "replicated history token no longer has a live document"
            raise ReplicaClosedError(message)
        payload = {
            "backend": document.engine.backend_id,
            "document": document.document_id,
            "payload": base64.b64encode(document.engine.encode_token(self.backend_token)).decode("ascii"),
            "schema": 1,
            "token_epoch": self.token_epoch,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @classmethod
    def decode(cls, document: ReplicatedDocument, token: bytes) -> ReplicationChangeToken:
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
            backend_token = document.engine.decode_token(base64.b64decode(encoded, validate=True))
        except (binascii.Error, TypeError, ValueError) as error:
            message = "replicated history token has corrupt backend data"
            raise ValueError(message) from error
        return cls(weakref.ref(document), backend_token, token_epoch)

    def plan_inverse(self) -> PreparedReplicationInverse | ConflictDetail:
        document = self.document()
        if document is None or document.closed:
            return ConflictDetail("replicated:closed", 0, 0)
        if self.token_epoch != document.token_epoch:
            return ConflictDetail(f"replicated:{document.document_id}:expired", self.token_epoch, document.token_epoch)
        inverse = document.engine.plan_inverse(self.backend_token)
        if isinstance(inverse, ConflictDetail):
            return inverse
        return PreparedReplicationInverse(inverse, self.token_epoch)

    def stage_inverse(self, inverse: PreparedReplicationInverse) -> None:
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
        participant.branch.stage_inverse(inverse.payload)

    def retain(self) -> ReplicationHistoryLease:
        """Retain the backend history needed by this token until the returned lease releases it."""
        document = self.document()
        if document is None or document.closed:
            message = "replicated history token no longer has a live document"
            raise ReplicaClosedError(message)
        retention = document.engine.retain_token(self.backend_token)
        return ReplicationHistoryLease(weakref.ref(document), retention)


@dataclass(slots=True)
class ReplicationHistoryLease:
    """Retain inverse metadata until :meth:`release` is called or this lease is collected."""

    document: weakref.ReferenceType[ReplicatedDocument]
    retention: object | None

    def release(self) -> None:
        """Release this token's compaction boundary; repeated calls do nothing."""
        retention = self.retention
        if retention is None:
            return
        self.retention = None
        document = self.document()
        if document is not None:
            document.engine.release_token(retention)

    def __del__(self) -> None:
        self.release()


class _ReplicationParticipant:
    def __init__(
        self,
        document: ReplicatedDocument,
        *,
        remote: object | None = None,
        remote_update_id: str | None = None,
    ) -> None:
        self.document = document
        self.branch = document.engine.branch()
        self.remote = remote
        self.remote_update_id = remote_update_id

    def prepare(self, view: TransactionView) -> object:
        if self.remote is not None:
            return self.remote
        base = self.branch.base
        if self.document.engine.version() != base:
            detail = ConflictDetail(self.document.identity, hash(base), hash(self.document.engine.version()))
            raise ReactiveConflictError(detail, f"{self.document.identity} changed before replicated prepare")
        return self.branch.prepare(base)

    def describe_change(self, prepared: object) -> TransactionContribution | None:
        backend_token = self.document.engine.change_token(prepared)
        if backend_token is None:
            return None
        token = ReplicationChangeToken(weakref.ref(self.document), backend_token, self.document.token_epoch)
        return TransactionContribution(self.document.identity, token, ChangeReport(participants=1))

    def apply(self, prepared: object) -> None:
        self.document.engine.apply(prepared)
        self.document._version_cell.write(self.document.engine.snapshot())

    def abort(self, prepared: object | None, cause: BaseException) -> None:
        return None

    def finalize(self, prepared: object) -> None:
        self.document._notify()
        if self.remote_update_id is None and self.document.engine.change_token(prepared) is not None:
            self.document._publish_update(prepared)


class ReplicatedDocument:
    """One immutable-snapshot replicated document. Calling :meth:`close` ends all access."""

    def __init__(self, document_id: str, engine: ReplicationEngine[Any, Any, Any, Any]) -> None:
        self.document_id = document_id
        self.engine = engine
        self.closed = False
        self._version_cell = _Cell(engine.snapshot(), address=f"replicated:{document_id}")
        self._listeners: set[Any] = set()
        self._update_listeners: set[Callable[[ReplicationUpdate], None]] = set()
        self._pending_updates: deque[ReplicationUpdate] = deque(maxlen=_PENDING_UPDATE_LIMIT)
        self._seen_updates: deque[str] = deque(maxlen=_DEDUP_LIMIT)
        self._seen_update_ids: set[str] = set()
        self._token_epoch = 0
        self._dropped_updates = 0
        self._resync_required = False

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
    def resync_required(self) -> bool:
        """Whether outbound overflow has left the pending buffer unable to carry a peer forward."""
        return self._resync_required

    @property
    def dropped_update_count(self) -> int:
        """Return how many outbound updates overflow discarded since the last acknowledged resync."""
        return self._dropped_updates

    @property
    def subscription_count(self) -> int:
        """Return the snapshot and outbound listener authorities owned by this document."""
        return len(self._listeners) + len(self._update_listeners)

    @property
    def deduplication_count(self) -> int:
        """Return the bounded number of remote update identities retained for deduplication."""
        return len(self._seen_update_ids)

    def snapshot(self) -> Any:
        self._ensure_open()
        self._version_cell.read()
        participant = action_participant(self)
        return (
            participant.branch.snapshot()
            if isinstance(participant, _ReplicationParticipant)
            else self.engine.snapshot()
        )

    def counter(self, path: str) -> ReplicatedCounter:
        self._require_container("counter")
        return ReplicatedCounter(self, path)

    def set(self, path: str) -> ReplicatedSet:
        self._require_container("set")
        return ReplicatedSet(self, path)

    def text(self, path: str) -> ReplicatedText:
        self._require_container("text")
        return ReplicatedText(self, path)

    def list(self, path: str) -> ReplicatedList:
        self._require_container("list")
        return ReplicatedList(self, path)

    def movable_list(self, path: str) -> ReplicatedMovableList:
        self._require_container("movable")
        return ReplicatedMovableList(self, path)

    def map(self, path: str) -> ReplicatedMap:
        self._require_container("map")
        return ReplicatedMap(self, path)

    def tree(self, path: str) -> ReplicatedTree:
        self._require_container("tree")
        return ReplicatedTree(self, path)

    def export_since(self, version: object | None = None) -> bytes:
        self._ensure_open()
        update = ReplicationUpdate.create(
            document_id=self.document_id,
            backend_id=self.engine.backend_id,
            source_replica_id=self.engine.replica_id,
            payload=self.engine.export_since(version),
            origin_action_id=None,
        )
        return update.encode()

    def version(self) -> object:
        """Return the backend-opaque version a peer can pass back to :meth:`export_since`."""
        self._ensure_open()
        return self.engine.version()

    def import_update(self, update: bytes) -> None:
        self._ensure_open()
        envelope = ReplicationUpdate.decode(update)
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
            kind=ActionPurpose.REMOTE,
            cause=cause,
            root_action_id=envelope.origin_action_id,
            actor=ActorRef("replica", envelope.source_replica_id),
            metadata={"document_id": self.document_id, "update_id": update_id},
        )
        with transaction(action_context=context):
            joined = enlist(
                self,
                lambda: _ReplicationParticipant(self, remote=prepared, remote_update_id=update_id),
            )
            assert joined is not None
        self._remember_update(update_id)

    def subscribe(self, callback: Callable[[Any], None]) -> Callable[[], None]:
        """Receive each committed snapshot until the returned function or document closes it."""
        self._ensure_open()
        self._listeners.add(callback)

        def unsubscribe() -> None:
            self._listeners.discard(callback)

        return unsubscribe

    def subscribe_updates(self, callback: Callable[[ReplicationUpdate], None]) -> Callable[[], None]:
        """Receive committed local updates until the returned function or document closes it."""
        self._ensure_open()
        self._update_listeners.add(callback)

        def unsubscribe() -> None:
            self._update_listeners.discard(callback)

        return unsubscribe

    def drain_updates(self) -> tuple[ReplicationUpdate, ...]:
        """Take committed outbound updates retained for an application-owned transport.

        Raises :class:`ReplicationResyncRequiredError` once this bounded buffer has overflowed,
        rather than returning a stream known to have a hole in it. Recover with
        :meth:`export_since` from the peer's version, then :meth:`acknowledge_resync`.
        :meth:`subscribe_updates` is the delivery path that never drops.
        """
        self._ensure_open()
        if self._resync_required:
            message = (
                f"replicated document {self.document_id!r} dropped {self._dropped_updates} outbound "
                "updates; export from the peer's version and acknowledge the resync"
            )
            raise ReplicationResyncRequiredError(message)
        updates = tuple(self._pending_updates)
        self._pending_updates.clear()
        return updates

    def acknowledge_resync(self) -> None:
        """Clear an overflow, discarding the partial updates it left behind.

        Call once :meth:`export_since` has carried the peer across the gap; the retained
        updates go with it because that export already supersedes them.
        """
        self._ensure_open()
        self._pending_updates.clear()
        self._dropped_updates = 0
        self._resync_required = False

    def compact_history(self) -> None:
        """Compact backend history without crossing any retained token lease."""
        self._ensure_open()
        if isinstance(action_participant(self), _ReplicationParticipant):
            message = "replicated history cannot be compacted inside a transaction that edits the document"
            raise TypeError(message)
        self.engine.compact()

    def checkpoint(self) -> bytes:
        """Export a self-contained, authenticated-by-the-host checkpoint envelope."""
        return self.export_since()

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
        self._dropped_updates = 0
        self._resync_required = False

    def _participant(self) -> _ReplicationParticipant:
        self._ensure_open()
        participant = enlist(self, lambda: _ReplicationParticipant(self))
        if participant is None:
            message = "replicated mutations require a Squid transaction"
            raise RuntimeError(message)
        return participant

    def _notify(self) -> None:
        for callback in tuple(self._listeners):
            callback(self.snapshot())

    def _publish_update(self, prepared: object) -> None:
        from squid_reactivity import current_action

        context = current_action()
        update = ReplicationUpdate.create(
            document_id=self.document_id,
            backend_id=self.engine.backend_id,
            source_replica_id=self.engine.replica_id,
            payload=self.engine.encode_prepared(prepared),
            origin_action_id=None if context is None else context.action_id,
        )
        if len(self._pending_updates) == _PENDING_UPDATE_LIMIT:
            # The deque would evict its oldest entry without a word. The loss itself is
            # recoverable -- export_since can still answer any version -- so what has to be
            # preserved is the fact that it happened.
            self._dropped_updates += 1
            self._resync_required = True
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
            raise ReplicaClosedError(message)

    def _require_container(self, kind: str) -> None:
        self._ensure_open()
        if kind not in self.engine.container_kinds:
            message = f"backend {self.engine.backend_id!r} does not support {kind!r} containers"
            raise UnsupportedReplicationContainerError(message)


@dataclass(frozen=True, slots=True)
class ReplicatedCounter:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> int:
        return self.document.snapshot().counter(self.path)

    def increment(self, amount: int = 1) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int):
            message = "counter increments must be integers"
            raise TypeError(message)
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("increment", self.path, value=amount))


@dataclass(frozen=True, slots=True)
class ReplicatedSet:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> frozenset[str]:
        return self.document.snapshot().tagged_set(self.path)

    def add(self, value: str) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("add", self.path, value=value))

    def discard(self, value: str) -> None:
        participant = self.document._participant()
        tags = self.document.engine.visible_tags(self.path, value, participant.branch.operations)
        participant.branch.apply(self.document.engine.make_operation("remove", self.path, value=value, tags=tags))


@dataclass(frozen=True, slots=True)
class ReplicatedText:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> str:
        return self.document.snapshot().text(self.path)

    def insert(self, index: int, value: str) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("text_insert", self.path, index=index, value=value)
        )

    def delete(self, index: int, count: int = 1) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("text_delete", self.path, index=index, count=count)
        )


@dataclass(frozen=True, slots=True)
class ReplicatedList:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> tuple[ReplicatedValue, ...]:
        return self.document.snapshot().sequence(self.path)

    def insert(self, index: int, value: object) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("list_insert", self.path, index=index, value=freeze_value(value))
        )

    def delete(self, index: int, count: int = 1) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("list_delete", self.path, index=index, count=count)
        )

    def replace(self, index: int, value: object) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("list_replace", self.path, index=index, value=freeze_value(value))
        )


@dataclass(frozen=True, slots=True)
class ReplicatedMovableList:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> tuple[ReplicatedItem, ...]:
        return self.document.snapshot().movable(self.path)

    def insert(self, index: int, value: object, *, item_id: uuid.UUID | None = None) -> uuid.UUID:
        logical_id = item_id or uuid.uuid7()
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation(
                "movable_insert", self.path, index=index, item_id=str(logical_id), value=freeze_value(value)
            )
        )
        return logical_id

    def delete(self, item_id: uuid.UUID) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("movable_delete", self.path, item_id=str(item_id)))

    def move(self, item_id: uuid.UUID, index: int) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("movable_move", self.path, item_id=str(item_id), index=index)
        )

    def replace(self, item_id: uuid.UUID, value: object) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation(
                "movable_replace", self.path, item_id=str(item_id), value=freeze_value(value)
            )
        )


@dataclass(frozen=True, slots=True)
class ReplicatedMap:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> Mapping[str, ReplicatedValue]:
        return self.document.snapshot().mapping(self.path)

    def set(self, key: str, value: object) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation("map_set", self.path, key=key, value=freeze_value(value))
        )

    def delete(self, key: str) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("map_delete", self.path, key=key))


@dataclass(frozen=True, slots=True)
class ReplicatedTree:
    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> ReplicatedTreeSnapshot:
        return self.document.snapshot().tree(self.path)

    def create(
        self,
        *,
        parent_id: uuid.UUID | None = None,
        index: int | None = None,
        metadata: Mapping[str, object] | None = None,
        node_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        logical_id = node_id or uuid.uuid7()
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation(
                "tree_create",
                self.path,
                node_id=str(logical_id),
                parent_id=None if parent_id is None else str(parent_id),
                index=index,
                metadata=freeze_value(dict(metadata or {})),
            )
        )
        return logical_id

    def move(self, node_id: uuid.UUID, *, parent_id: uuid.UUID | None = None, index: int | None = None) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation(
                "tree_move",
                self.path,
                node_id=str(node_id),
                parent_id=None if parent_id is None else str(parent_id),
                index=index,
            )
        )

    def set_metadata(self, node_id: uuid.UUID, key: str, value: object) -> None:
        participant = self.document._participant()
        participant.branch.apply(
            self.document.engine.make_operation(
                "tree_metadata", self.path, node_id=str(node_id), key=key, value=freeze_value(value)
            )
        )

    def delete(self, node_id: uuid.UUID) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("tree_delete", self.path, node_id=str(node_id)))


class Replica:
    """Own replicated documents and listeners. Calling :meth:`close` closes every document.

    `replica_id` names an incarnation, not a machine. A restarted process that reuses one must
    import peer state before its first local mutation so the log can restore the operation
    clock; mutating first re-mints identities its peers already hold, and that is refused.
    """

    def __init__(self, replica_id: str, *, backend: ReplicationBackend) -> None:
        self.replica_id = replica_id
        self.backend = backend
        self._documents: dict[str, ReplicatedDocument] = {}
        self.closed = False

    def open(self, document_id: str) -> ReplicatedDocument:
        if self.closed:
            message = "replicated scope is closed"
            raise ReplicaClosedError(message)
        document = self._documents.get(document_id)
        if document is None:
            engine = self.backend.open_engine(self.replica_id, document_id)
            document = self._documents[document_id] = ReplicatedDocument(document_id, engine)
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

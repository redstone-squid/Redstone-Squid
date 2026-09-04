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
    """Raised by every `Replica` or `ReplicatedDocument` method, and by their tokens, after `close()`."""


class ReplicationResyncRequiredError(ReplicationError, RuntimeError):
    """The bounded outbound buffer overflowed, so what it still holds is an incomplete history.

    Recover by exporting from the peer's version and acknowledging the resync. Nothing is lost
    permanently -- the operation log can still answer any version -- but the buffer alone can
    no longer carry a peer forward.
    """


class ReplicationCorruptUpdateError(ReplicationError, ValueError):
    """A backend update or durable token failed structural or native decoding."""


class ReplicationBackendIntegrityError(ReplicationError, RuntimeError):
    """The backend rejected work it had already validated: an apply, export, compaction or planned inverse."""


class UnsupportedReplicationContainerError(ReplicationError, TypeError):
    """A container accessor asked for a kind missing from the engine's `container_kinds`."""


@dataclass(frozen=True, slots=True)
class PreparedReplicationInverse:
    """A backend inverse from `ReplicationChangeToken.plan_inverse`, valid for one `token_epoch`."""

    payload: object
    """What `ReplicationEngine.plan_inverse` returned; only `ReplicaBranch.stage_inverse` reads it."""
    token_epoch: int
    """`ReplicatedDocument.token_epoch` at planning time; `stage_inverse` refuses any other."""


@dataclass(frozen=True, slots=True)
class ReplicationChangeToken:
    """The `ChangeToken` a replicated document contributes to each committed action.

    Holds its document weakly. Once that document is closed or collected, `plan_inverse()`
    reports a `replicated:closed` conflict and `encode()`/`retain()` raise `ReplicaClosedError`.
    """

    document: weakref.ReferenceType[ReplicatedDocument]
    backend_token: object
    """The engine's `change_token` result; meaningful only to the same `backend_id`."""
    token_epoch: int
    """`ReplicatedDocument.token_epoch` when the action committed."""

    def encode(self) -> bytes:
        """Serialise for durable history; `decode` reloads it on any replica of the same document.

        Raises `ReplicaClosedError` once the document is closed or gone, and `ValueError` when
        the backend token exceeds its size limit.
        """
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
        """Reload `encode` bytes against the live instance of the document they came from.

        Raises `ValueError` for corrupt bytes, an unknown schema, or a token minted for another
        backend or document, and `ReplicaClosedError` when `document` is closed. The epoch is
        taken from the bytes, so a token from before `expire_history_tokens()` still conflicts.
        """
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
        """Ask the engine for this token's inverse without staging it.

        Answers a `replicated:closed` conflict once the document is closed or collected, a
        `replicated:<document_id>:expired` conflict when `expire_history_tokens()` has run
        since the token was minted, and otherwise whatever `ReplicationEngine.plan_inverse`
        reports.
        """
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
        """Stage a `plan_inverse` result on the current transaction's branch of the document.

        Raises `ReactiveConflictError` (`replicated:closed` or `replicated:<document_id>:expired`)
        when the document is gone or `inverse.token_epoch` is stale, `RuntimeError` outside a
        Squid transaction, and `TypeError` when the payload belongs to another backend.
        """
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
        """Pin the history `plan_inverse` needs past `compact_history()` until the lease releases it.

        Raises `ReplicaClosedError` once the document is closed or gone, and `ValueError` when
        the backend has already discarded that history.
        """
        document = self.document()
        if document is None or document.closed:
            message = "replicated history token no longer has a live document"
            raise ReplicaClosedError(message)
        retention = document.engine.retain_token(self.backend_token)
        return ReplicationHistoryLease(weakref.ref(document), retention)


@dataclass(slots=True)
class ReplicationHistoryLease:
    """A `ReplicationEngine.retain_token` handle that `release()` or garbage collection gives back.

    While it is held, `ReplicatedDocument.compact_history()` keeps everything at or after the
    retained token.
    """

    document: weakref.ReferenceType[ReplicatedDocument]
    retention: object | None
    """The engine's handle; `None` once released."""

    def release(self) -> None:
        """Give the handle back; repeated calls and a collected document are no-ops."""
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
    """One transaction's enlistment of one document: a branch for local edits, or a remote payload.

    A remote participant skips the base check in `prepare` and never publishes what it applies.
    """

    def __init__(
        self,
        document: ReplicatedDocument,
        *,
        remote_payload: bytes | None = None,
        remote_update_id: str | None = None,
    ) -> None:
        self.document = document
        self.branch = document.engine.branch()
        self.remote_payload = remote_payload
        self.remote_update_id = remote_update_id

    def prepare(self, view: TransactionView) -> object:
        if self.remote_payload is not None:
            return self.document.engine.prepare_remote(self.remote_payload)
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
        if self.document.engine.change_token(prepared) is None:
            return
        self.document.engine.apply(prepared)
        self.document._version_cell.write(self.document.engine.snapshot())

    def abort(self, prepared: object | None, cause: BaseException) -> None:
        return None

    def finalize(self, prepared: object) -> None:
        if self.document.engine.change_token(prepared) is None:
            return
        self.document._notify()
        if self.remote_update_id is None and self.document.engine.change_token(prepared) is not None:
            self.document._publish_update(prepared)


class ReplicatedDocument:
    """One engine's state as a reactive cell of snapshots; `close()` ends every read, mutation and subscription.

    `Replica.open` creates it; after `close()` every method raises `ReplicaClosedError`.
    Container mutations stage onto the enclosing Squid transaction (`RuntimeError` outside one)
    and reach the engine at commit; a committed local action is published to
    `subscribe_updates` callbacks and the `drain_updates` buffer.
    """

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
        """`replicated:<document_id>`: the participant identity conflicts and contributions report."""
        return f"replicated:{self.document_id}"

    @property
    def token_epoch(self) -> int:
        """Bumped by `expire_history_tokens()`; a `ReplicationChangeToken` from an older epoch conflicts."""
        return self._token_epoch

    @property
    def pending_update_count(self) -> int:
        """Envelopes waiting for `drain_updates()`; at most 1 000 are kept."""
        return len(self._pending_updates)

    @property
    def resync_required(self) -> bool:
        """True once the outbound buffer has dropped an update; `drain_updates()` raises until `acknowledge_resync()`."""
        return self._resync_required

    @property
    def dropped_update_count(self) -> int:
        """Outbound updates the full buffer discarded since the last `acknowledge_resync()`."""
        return self._dropped_updates

    @property
    def subscription_count(self) -> int:
        """Live `subscribe()` plus `subscribe_updates()` callbacks."""
        return len(self._listeners) + len(self._update_listeners)

    @property
    def deduplication_count(self) -> int:
        """Remote `update_id`s remembered so a redelivery is skipped; the newest 10 000 are kept."""
        return len(self._seen_update_ids)

    def snapshot(self) -> Any:
        """The committed engine snapshot, or the current transaction's branch view once it has enlisted.

        Reading inside a computation subscribes it to the document's cell.
        """
        self._ensure_open()
        self._version_cell.read()
        participant = action_participant(self)
        return (
            participant.branch.snapshot()
            if isinstance(participant, _ReplicationParticipant)
            else self.engine.snapshot()
        )

    def counter(self, path: str) -> ReplicatedCounter:
        """A handle on the counter at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("counter")
        return ReplicatedCounter(self, path)

    def set(self, path: str) -> ReplicatedSet:
        """A handle on the set at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("set")
        return ReplicatedSet(self, path)

    def text(self, path: str) -> ReplicatedText:
        """A handle on the text at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("text")
        return ReplicatedText(self, path)

    def list(self, path: str) -> ReplicatedList:
        """A handle on the list at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("list")
        return ReplicatedList(self, path)

    def movable_list(self, path: str) -> ReplicatedMovableList:
        """A handle on the movable list at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("movable")
        return ReplicatedMovableList(self, path)

    def map(self, path: str) -> ReplicatedMap:
        """A handle on the map at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("map")
        return ReplicatedMap(self, path)

    def tree(self, path: str) -> ReplicatedTree:
        """A handle on the tree at `path`; `UnsupportedReplicationContainerError` if the backend has none."""
        self._require_container("tree")
        return ReplicatedTree(self, path)

    def export_since(self, version: object | None = None) -> bytes:
        """Everything a peer at `version` lacks, as an encoded `ReplicationUpdate` with no origin action.

        `None` exports a self-contained snapshot. Raises what `ReplicationEngine.export_since`
        raises (`TypeError`, `ValueError`, `ReplicationCorruptUpdateError`), and `ValueError`
        when the envelope exceeds 1 500 000 bytes.
        """
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
        """The backend-opaque version a peer passes back to `export_since`; hashable and equality-comparable."""
        self._ensure_open()
        return self.engine.version()

    def import_update(self, update: bytes) -> None:
        """Commit a peer's `export_since` or `drain_updates` envelope in its own `REMOTE` transaction.

        An `update_id` among the last 10 000 seen is skipped without a transaction. Raises
        `ValueError` for an envelope that does not decode or that names another document or
        backend, and `ReplicationCorruptUpdateError` or `ValueError` from
        `ReplicationEngine.prepare_remote` for a bad payload.
        """
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
                lambda: _ReplicationParticipant(self, remote_payload=envelope.payload, remote_update_id=update_id),
            )
            assert joined is not None
        self._remember_update(update_id)

    def subscribe(self, callback: Callable[[Any], None]) -> Callable[[], None]:
        """Receive each committed snapshot, synchronously at finalize, until the returned function or `close()` stops it."""
        self._ensure_open()
        self._listeners.add(callback)

        def unsubscribe() -> None:
            self._listeners.discard(callback)

        return unsubscribe

    def subscribe_updates(self, callback: Callable[[ReplicationUpdate], None]) -> Callable[[], None]:
        """Receive each locally committed envelope until the returned function or `close()` stops it.

        Imported updates are not re-published. Unlike `drain_updates`, this path never drops.
        """
        self._ensure_open()
        self._update_listeners.add(callback)

        def unsubscribe() -> None:
            self._update_listeners.discard(callback)

        return unsubscribe

    def drain_updates(self) -> tuple[ReplicationUpdate, ...]:
        """Take the buffered outbound envelopes, oldest first, for an application-owned transport.

        Raises `ReplicationResyncRequiredError` once the 1 000-envelope buffer has overflowed,
        rather than returning a stream with a hole in it. Recover with `export_since` from the
        peer's version, then `acknowledge_resync`.
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

        Call once `export_since` has carried the peer across the gap; the buffered updates go
        with it because that export already supersedes them.
        """
        self._ensure_open()
        self._pending_updates.clear()
        self._dropped_updates = 0
        self._resync_required = False

    def compact_history(self) -> None:
        """Discard engine history older than every live `ReplicationHistoryLease`.

        Afterwards an unretained token from before that boundary plans an expired conflict.
        Raises `TypeError` inside a transaction that has already enlisted this document, and
        `ReplicationBackendIntegrityError` when the engine cannot reach a retained boundary.
        """
        self._ensure_open()
        if isinstance(action_participant(self), _ReplicationParticipant):
            message = "replicated history cannot be compacted inside a transaction that edits the document"
            raise TypeError(message)
        self.engine.compact()

    def checkpoint(self) -> bytes:
        """`export_since(None)`: a self-contained snapshot envelope any replica of this document can import.

        The envelope carries a payload hash, not a signature; authenticating the sender is the
        transport's job.
        """
        return self.export_since()

    def expire_history_tokens(self) -> None:
        """Bump `token_epoch` so every existing `ReplicationChangeToken` plans an expired conflict.

        Call before `compact_history()` when the history those tokens need is not retained.
        """
        self._ensure_open()
        self._token_epoch += 1

    def close(self) -> None:
        """Idempotent. Drops listeners, buffered updates and the deduplication window; the engine is left as is."""
        self.closed = True
        self._listeners.clear()
        self._update_listeners.clear()
        self._pending_updates.clear()
        self._seen_updates.clear()
        self._seen_update_ids.clear()
        self._dropped_updates = 0
        self._resync_required = False

    def _participant(self) -> _ReplicationParticipant:
        """Enlist in the current transaction, once per document; `RuntimeError` outside one."""
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
    """The integer counter at `path`: per-replica totals summed on read.

    `increment()` stages one `increment` operation on the current transaction (`RuntimeError`
    outside one); `value` reads through `ReplicatedDocument.snapshot()`.
    """

    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> int:
        return self.document.snapshot().counter(self.path)

    def increment(self, amount: int = 1) -> None:
        """Raises `TypeError` for anything but an `int`; `bool` is refused."""
        if isinstance(amount, bool) or not isinstance(amount, int):
            message = "counter increments must be integers"
            raise TypeError(message)
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("increment", self.path, value=amount))


@dataclass(frozen=True, slots=True)
class ReplicatedSet:
    """The observed-remove set of strings at `path`; `discard()` removes only the adds it observed.

    `add()` stages an `add` and `discard()` a `remove` tagged with the adds visible to it, so a
    concurrent add on another replica survives. Both stage on the current transaction
    (`RuntimeError` outside one); a `remove` sees its own transaction's adds.
    """

    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> frozenset[str]:
        return self.document.snapshot().tagged_set(self.path)

    def add(self, value: str) -> None:
        participant = self.document._participant()
        participant.branch.apply(self.document.engine.make_operation("add", self.path, value=value))

    def discard(self, value: str) -> None:
        """Discarding a non-member stages a `remove` with no tags, which changes nothing."""
        participant = self.document._participant()
        tags = self.document.engine.visible_tags(self.path, value, participant.branch.operations)
        participant.branch.apply(self.document.engine.make_operation("remove", self.path, value=value, tags=tags))


@dataclass(frozen=True, slots=True)
class ReplicatedText:
    """The collaborative string at `path`, indexed by code point.

    `insert()` and `delete()` stage `text_insert`/`text_delete` on the current transaction
    (`RuntimeError` outside one); an index past the end raises `ValueError` at staging. A
    history inverse restores the whole container by diff rather than op by op.
    """

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
    """The index-addressed sequence of JSON values at `path`.

    `insert()`, `delete()` and `replace()` stage `list_insert`/`list_delete`/`list_replace` on
    the current transaction (`RuntimeError` outside one). Values pass through `freeze_value`
    (`TypeError`/`ValueError` outside JSON, 16 levels, int64). Loro raises `IndexError` for an
    index past the visible end.
    """

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
    """The sequence at `path` whose items keep a stable `item_id` across `move()`.

    `insert()`, `delete()`, `move()` and `replace()` stage `movable_*` operations on the
    current transaction (`RuntimeError` outside one); an unknown `item_id` or an index past
    the end raises `ValueError` at staging. Values pass through `freeze_value`
    (`TypeError`/`ValueError` outside JSON, 16 levels, int64).
    """

    document: ReplicatedDocument
    path: str

    @property
    def value(self) -> tuple[ReplicatedItem, ...]:
        return self.document.snapshot().movable(self.path)

    def insert(self, index: int, value: object, *, item_id: uuid.UUID | None = None) -> uuid.UUID:
        """Returns the item's id: `item_id`, or a fresh UUIDv7."""
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
    """The string-keyed map of JSON values at `path`; each key is a last-writer-wins register.

    `set()` and `delete()` stage `map_set`/`map_delete` on the current transaction
    (`RuntimeError` outside one), recording the previous value so the action can be reversed;
    deleting an absent key is allowed. Values pass through `freeze_value`
    (`TypeError`/`ValueError` outside JSON, 16 levels, int64).
    """

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
    """The forest at `path`: nodes with a stable `node_id`, an ordered child list and per-key metadata.

    `create()`, `move()`, `set_metadata()` and `delete()` stage `tree_*` operations on the
    current transaction (`RuntimeError` outside one); all but `create` raise `ValueError` at
    staging for an unknown `node_id`. `delete` removes the node's whole subtree. Metadata
    values pass through `freeze_value` (`TypeError`/`ValueError` outside JSON, 16 levels,
    int64).
    """

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
        """Returns the node's id: `node_id`, or a fresh UUIDv7. `parent_id=None` makes a root; `index=None` appends."""
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
        """`parent_id=None` makes the node a root; `index=None` appends among its new siblings."""
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
    """Owns one engine per opened document; `close()` closes every document and refuses `open()`.

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
        """The same `ReplicatedDocument` for a repeated `document_id`; raises `ReplicaClosedError` after `close()`."""
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
        """Every `document_id` opened so far, in open order; empty after `close()`."""
        return tuple(self._documents)

    def close(self) -> None:
        for document in self._documents.values():
            document.close()
        self._documents.clear()
        self.closed = True

"""Reactive replicated documents integrated as Squid action participants."""

import weakref
from dataclasses import dataclass
from typing import Any

from squid_reactive.actions import (
    ActionContext,
    ActionKind,
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
    def __init__(self, document: ReplicatedDocument, *, remote: PreparedFakeUpdate | None = None) -> None:
        self.document = document
        self.branch = document.engine.branch()
        self.remote = remote

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


class ReplicatedDocument:
    """One immutable-snapshot replicated document. Calling :meth:`close` ends all access."""

    def __init__(self, document_id: str, engine: FakeEngine) -> None:
        self.document_id = document_id
        self.engine = engine
        self.closed = False
        self._version_cell = _Cell(engine.snapshot(), address=f"replicated:{document_id}")
        self._listeners: set[Any] = set()

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
        return self.engine.export_since(version)

    def import_update(self, update: bytes) -> None:
        self._ensure_open()
        prepared = self.engine.prepare_remote(update)
        context = ActionContext.create(f"Import {self.document_id}", kind=ActionKind.REMOTE)
        with transaction(action_context=context):
            joined = join_action(self, lambda: _ReplicationParticipant(self, remote=prepared))
            assert joined is not None

    def subscribe(self, callback) -> callable:
        self._listeners.add(callback)

        def unsubscribe() -> None:
            self._listeners.discard(callback)

        return unsubscribe

    def close(self) -> None:
        self.closed = True
        self._listeners.clear()

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

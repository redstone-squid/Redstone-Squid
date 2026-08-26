"""Backend-neutral replicated engine and staging contracts."""

from typing import Any, Protocol


class ReplicaBranch[SnapshotT, OperationT, PreparedT](Protocol):
    """An isolated branch whose prepared work has not changed canonical state."""

    def apply(self, operation: OperationT) -> None: ...

    def snapshot(self) -> SnapshotT: ...

    def prepare(self, base: object) -> PreparedT: ...


class ReplicationEngine[SnapshotT, OperationT, PreparedT, ChangeT](Protocol):
    """A CRDT backend hidden behind immutable snapshots and opaque causal tokens."""

    @property
    def backend_id(self) -> str: ...

    def snapshot(self) -> SnapshotT: ...

    def version(self) -> object: ...

    def branch(self) -> ReplicaBranch[SnapshotT, OperationT, PreparedT]: ...

    def apply(self, prepared: PreparedT) -> ChangeT: ...

    def prepare_remote(self, update: bytes) -> PreparedT: ...

    def export_since(self, version: object | None = None) -> bytes: ...


class ReplicationBackend(Protocol):
    """A configured engine factory reusable for every document in one replica."""

    @property
    def backend_id(self) -> str: ...

    def open_engine(
        self,
        replica_id: str,
        document_id: str,
    ) -> ReplicationEngine[Any, Any, Any, Any]: ...

"""Connect transactional documents to interchangeable replication backends.

A backend creates an engine for each document. The engine owns committed state;
a branch holds one transaction's edits until preparation validates them and apply
publishes them. Documents use these interfaces without exposing native backend
containers to application code.
"""

from typing import Any, Protocol

from squid_reactivity import ConflictDetail


class ReplicaBranch[SnapshotT, OperationT, PreparedT](Protocol):
    """Stage operations and read their effects without publishing canonical state.

    The base identifies the version at branch creation. The transaction coordinator
    checks that committed state still matches it before preparing a local commit.
    Calling prepare produces backend work; only the engine's apply publishes it.
    """

    def apply(self, operation: OperationT) -> None: ...

    def snapshot(self) -> SnapshotT: ...

    def prepare(self, base: object) -> PreparedT: ...

    def stage_inverse(self, inverse: object) -> None: ...

    @property
    def base(self) -> object: ...

    @property
    def operations(self) -> tuple[OperationT, ...]: ...


class ReplicationEngine[SnapshotT, OperationT, PreparedT, ChangeT](Protocol):
    """Expose a CRDT through immutable snapshots and opaque backend tokens.

    The prepare_remote method validates incoming bytes without publishing them. For a
    prepared no-op, change_token must return None: the document skips apply, invalidation, and
    notifications. Applying prepared work is the canonical commit boundary.

    Versions support incremental export; None requests a checkpoint. Inverse planning
    returns work to stage or a conflict without publishing state. Retention leases
    protect the history needed for inverses from compaction until explicitly released.
    """

    @property
    def backend_id(self) -> str: ...

    @property
    def replica_id(self) -> str: ...

    @property
    def container_kinds(self) -> frozenset[str]: ...

    def snapshot(self) -> SnapshotT: ...

    def version(self) -> object: ...

    def branch(self) -> ReplicaBranch[SnapshotT, OperationT, PreparedT]: ...

    def apply(self, prepared: PreparedT) -> ChangeT: ...

    def prepare_remote(self, update: bytes) -> PreparedT: ...

    def export_since(self, version: object | None = None) -> bytes: ...

    def change_token(self, prepared: PreparedT) -> object | None: ...

    def encode_token(self, token: object) -> bytes: ...

    def decode_token(self, token: bytes) -> object: ...

    def plan_inverse(self, token: object) -> object | ConflictDetail: ...

    def encode_prepared(self, prepared: PreparedT) -> bytes: ...

    def make_operation(self, kind: str, path: str, **data: Any) -> OperationT: ...

    def visible_tags(
        self,
        path: str,
        value: str,
        additions: tuple[OperationT, ...] = (),
    ) -> tuple[object, ...]: ...

    def retain_token(self, token: object) -> object: ...

    def release_token(self, retention: object) -> None: ...

    def compact(self) -> None: ...


class ReplicationBackend(Protocol):
    """A configured engine factory reusable for every document in one replica."""

    @property
    def backend_id(self) -> str: ...

    def open_engine(
        self,
        replica_id: str,
        document_id: str,
    ) -> Any: ...

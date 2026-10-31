"""Deterministic operation-log backend for counter and tagged-set conformance tests."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from squid_reactivity import ConflictDetail

_MAX_UPDATE_BYTES = 1_048_576
_MAX_OPERATIONS = 10_000
_KINDS = frozenset({"increment", "add", "remove", "restore"})


@dataclass(frozen=True, slots=True, order=True)
class OperationId:
    replica: str
    sequence: int

    def encode(self) -> str:
        return f"{self.replica}:{self.sequence}"

    @classmethod
    def decode(cls, value: str) -> OperationId:
        replica, sequence = value.rsplit(":", 1)
        return cls(replica, int(sequence))


@dataclass(frozen=True, slots=True)
class ReferenceOperation:
    identity: OperationId
    kind: str
    path: str
    value: str | int
    tags: tuple[OperationId, ...] = ()
    undoes: OperationId | None = None
    """The removal a ``restore`` reverses. Set on that kind alone."""


@dataclass(frozen=True, slots=True)
class ReferenceVersion:
    operations: frozenset[OperationId]


@dataclass(frozen=True, slots=True)
class ReferenceSnapshot:
    counters: tuple[tuple[str, int], ...]
    sets: tuple[tuple[str, frozenset[str]], ...]

    def counter(self, path: str) -> int:
        return dict(self.counters).get(path, 0)

    def tagged_set(self, path: str) -> frozenset[str]:
        return dict(self.sets).get(path, frozenset())


@dataclass(frozen=True, slots=True)
class PreparedReferenceUpdate:
    base: ReferenceVersion | None
    operations: tuple[ReferenceOperation, ...]


@dataclass(frozen=True, slots=True)
class ReferenceChange:
    operations: tuple[ReferenceOperation, ...]


class ReferenceBranch:
    def __init__(self, engine: ReferenceEngine) -> None:
        self._engine = engine
        self._base = engine.version()
        self._operations: list[ReferenceOperation] = []

    @property
    def base(self) -> ReferenceVersion:
        return self._base

    @property
    def operations(self) -> tuple[ReferenceOperation, ...]:
        return tuple(self._operations)

    def apply(self, operation: ReferenceOperation) -> None:
        self._operations.append(operation)

    def snapshot(self) -> ReferenceSnapshot:
        return self._engine.snapshot_with(self._operations)

    def prepare(self, base: object) -> PreparedReferenceUpdate:
        assert isinstance(base, ReferenceVersion)
        return PreparedReferenceUpdate(base, tuple(self._operations))

    def stage_inverse(self, inverse: object) -> None:
        if not isinstance(inverse, tuple) or not all(isinstance(item, ReferenceOperation) for item in inverse):
            message = "reference inverse has the wrong backend type"
            raise TypeError(message)
        self._operations.extend(inverse)


class ReferenceEngine:
    """A deterministic convergent operation set used to prove Squid integration semantics."""

    backend_id = "squid-reference-v1"
    container_kinds = frozenset({"counter", "set"})

    def __init__(self, replica_id: str) -> None:
        self.replica_id = replica_id
        self._next_sequence = 0
        self._operations: dict[OperationId, ReferenceOperation] = {}
        self._retentions: set[object] = set()

    def operation(
        self,
        kind: str,
        path: str,
        value: str | int,
        tags: tuple[OperationId, ...] = (),
        undoes: OperationId | None = None,
    ) -> ReferenceOperation:
        self._next_sequence += 1
        return ReferenceOperation(OperationId(self.replica_id, self._next_sequence), kind, path, value, tags, undoes)

    def make_operation(self, kind: str, path: str, **data: Any) -> ReferenceOperation:
        return self.operation(
            kind,
            path,
            data["value"],
            tuple(data.get("tags", ())),
            data.get("undoes"),
        )

    def version(self) -> ReferenceVersion:
        return ReferenceVersion(frozenset(self._operations))

    def branch(self) -> ReferenceBranch:
        return ReferenceBranch(self)

    def snapshot(self) -> ReferenceSnapshot:
        return self.snapshot_with(())

    def snapshot_with(self, additions: tuple[ReferenceOperation, ...] | list[ReferenceOperation]) -> ReferenceSnapshot:
        operations = {**self._operations, **{operation.identity: operation for operation in additions}}
        ordered = sorted(operations.values(), key=lambda item: item.identity)
        counters: dict[str, int] = {}
        added: dict[tuple[str, str], set[OperationId]] = {}
        for operation in ordered:
            if operation.kind == "increment":
                assert isinstance(operation.value, int)
                counters[operation.path] = counters.get(operation.path, 0) + operation.value
            elif operation.kind == "add":
                assert isinstance(operation.value, str)
                added.setdefault((operation.path, operation.value), set()).add(operation.identity)
        standing = self._standing_removals(ordered)
        sets: dict[str, set[str]] = {}
        for (path, value), tags in added.items():
            if any(not standing.get(tag) for tag in tags):
                sets.setdefault(path, set()).add(value)
        return ReferenceSnapshot(
            tuple(sorted(counters.items())),
            tuple((path, frozenset(values)) for path, values in sorted(sets.items())),
        )

    def visible_tags(
        self, path: str, value: str, additions: tuple[ReferenceOperation, ...] = ()
    ) -> tuple[OperationId, ...]:
        operations = {**self._operations, **{operation.identity: operation for operation in additions}}
        standing = self._standing_removals(operations.values())
        return tuple(
            operation.identity
            for operation in operations.values()
            if operation.kind == "add"
            and operation.path == path
            and operation.value == value
            and not standing.get(operation.identity)
        )

    def _standing_removals(self, operations: Iterable[ReferenceOperation]) -> dict[OperationId, set[OperationId]]:
        """Map each removed add-tag to the removals still standing against it.

        A tag is live when its entry is empty or absent. Keyed by which removal killed it
        rather than flattened into one tombstone set, so undoing one replica's removal
        cancels that removal alone and leaves a concurrent one in force.
        """
        removals: dict[OperationId, set[OperationId]] = {}
        reversed_removals: set[OperationId] = set()
        for operation in operations:
            if operation.kind == "remove":
                for tag in operation.tags:
                    removals.setdefault(tag, set()).add(operation.identity)
            elif operation.kind == "restore":
                assert operation.undoes is not None
                reversed_removals.add(operation.undoes)
        return {tag: killers - reversed_removals for tag, killers in removals.items()}

    def apply(self, prepared: PreparedReferenceUpdate) -> ReferenceChange:
        # Checked before anything is recorded, so a rejected update leaves the log untouched.
        for operation in prepared.operations:
            existing = self._operations.get(operation.identity)
            if existing is not None and existing != operation:
                message = f"replicated operation identity {operation.identity.encode()!r} was reused"
                raise ValueError(message)
        for operation in prepared.operations:
            self._operations.setdefault(operation.identity, operation)
            if operation.identity.replica == self.replica_id:
                # Restores the clock from the log. A restarted process reusing its replica ID
                # would otherwise re-mint identities its peers already hold, and the duplicate
                # dropped as already-known is its own new work rather than the stale copy.
                self._next_sequence = max(self._next_sequence, operation.identity.sequence)
        return ReferenceChange(prepared.operations)

    def prepare_remote(self, update: bytes) -> PreparedReferenceUpdate:
        operations = self._decode_envelope(update, kind="update")
        return PreparedReferenceUpdate(None, operations)

    def encode_token(self, operations: tuple[ReferenceOperation, ...]) -> bytes:
        """Encode one action's opaque change token for durable history tests."""
        return self._encode_envelope("history-token", operations)

    def change_token(self, prepared: PreparedReferenceUpdate) -> object | None:
        return prepared.operations or None

    def decode_token(self, token: bytes) -> tuple[ReferenceOperation, ...]:
        """Decode a schema-checked action token without applying it."""
        return self._decode_envelope(token, kind="history-token")

    def plan_inverse(self, token: object) -> object | ConflictDetail:
        if not isinstance(token, tuple) or not all(isinstance(item, ReferenceOperation) for item in token):
            return ConflictDetail("replicated:reference:token", 0, 0)
        inverse: list[ReferenceOperation] = []
        for operation in reversed(token):
            if operation.kind == "increment":
                assert isinstance(operation.value, int)
                inverse.append(self.operation("increment", operation.path, -operation.value))
            elif operation.kind == "add":
                inverse.append(self.operation("remove", operation.path, operation.value, (operation.identity,)))
            elif operation.kind == "remove":
                inverse.append(
                    self.operation("restore", operation.path, operation.value, operation.tags, operation.identity)
                )
            elif operation.kind == "restore":
                inverse.append(self.operation("remove", operation.path, operation.value, operation.tags))
            else:
                return ConflictDetail(f"replicated:{operation.path}", 0, 0)
        return tuple(inverse)

    def encode_prepared(self, prepared: PreparedReferenceUpdate) -> bytes:
        return self.encode_update(prepared.operations)

    def retain_token(self, token: object) -> object:
        retention = object()
        self._retentions.add(retention)
        return retention

    def release_token(self, retention: object) -> None:
        self._retentions.discard(retention)

    def compact(self) -> None:
        """Keep the deterministic reference log unchanged; it is a bounded test backend."""

    def export_since(self, version: object | None = None) -> bytes:
        known = version.operations if isinstance(version, ReferenceVersion) else frozenset()
        operations = tuple(
            operation for identity, operation in sorted(self._operations.items()) if identity not in known
        )
        return self._encode_envelope("update", operations)

    def encode_update(self, operations: tuple[ReferenceOperation, ...]) -> bytes:
        """Encode exactly one committed participant change for outbound transport."""
        return self._encode_envelope("update", operations)

    def _decode_envelope(self, data: bytes, *, kind: str) -> tuple[ReferenceOperation, ...]:
        if len(data) > _MAX_UPDATE_BYTES:
            message = "replicated update exceeds the maximum encoded size"
            raise ValueError(message)
        payload: Any = json.loads(data)
        if not isinstance(payload, dict):
            message = "replicated update must be an object"
            raise TypeError(message)
        if payload.get("backend") != self.backend_id or payload.get("schema") != 1 or payload.get("kind") != kind:
            message = "replicated update has the wrong backend or schema"
            raise ValueError(message)
        encoded = payload.get("operations")
        if not isinstance(encoded, list) or len(encoded) > _MAX_OPERATIONS:
            message = "replicated update has an invalid operation collection"
            raise ValueError(message)
        operations: list[ReferenceOperation] = []
        identities: dict[OperationId, ReferenceOperation] = {}
        for item in encoded:
            if not isinstance(item, dict):
                message = "replicated operation must be an object"
                raise TypeError(message)
            operation = self._decode_operation(item)
            existing = identities.get(operation.identity) or self._operations.get(operation.identity)
            if existing is not None and existing != operation:
                message = f"replicated operation identity {operation.identity.encode()!r} was reused"
                raise ValueError(message)
            identities[operation.identity] = operation
            operations.append(operation)
        return tuple(operations)

    def _decode_operation(self, item: dict[str, Any]) -> ReferenceOperation:
        identity = OperationId.decode(item["id"])
        kind = item["kind"]
        path = item["path"]
        value = item["value"]
        tags = item.get("tags", ())
        undoes = item.get("undoes")
        if (
            kind not in _KINDS
            or not isinstance(path, str)
            or not isinstance(tags, list)
            or not isinstance(undoes, str | None)
        ):
            message = "replicated operation has invalid fields"
            raise ValueError(message)
        if (kind == "increment" and not isinstance(value, int)) or (
            kind in {"add", "remove", "restore"} and not isinstance(value, str)
        ):
            message = "replicated operation value does not match its kind"
            raise ValueError(message)
        # Naming a reversed removal from any other kind would cancel a removal that nothing
        # claims to be undoing, so the field and the kind have to agree in both directions.
        if (kind == "restore") != (undoes is not None):
            message = "replicated operation names a reversed removal it does not match"
            raise ValueError(message)
        return ReferenceOperation(
            identity,
            kind,
            path,
            value,
            tuple(OperationId.decode(tag) for tag in tags),
            None if undoes is None else OperationId.decode(undoes),
        )

    def _encode_envelope(self, kind: str, operations: tuple[ReferenceOperation, ...]) -> bytes:
        payload = {
            "backend": self.backend_id,
            "schema": 1,
            "kind": kind,
            "operations": [
                {
                    "id": operation.identity.encode(),
                    "kind": operation.kind,
                    "path": operation.path,
                    "value": operation.value,
                    "tags": [tag.encode() for tag in operation.tags],
                    "undoes": None if operation.undoes is None else operation.undoes.encode(),
                }
                for operation in operations
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class ReferenceBackend:
    """Create bounded deterministic engines for conformance tests and examples."""

    backend_id = ReferenceEngine.backend_id

    def open_engine(self, replica_id: str, document_id: str) -> ReferenceEngine:
        return ReferenceEngine(replica_id)

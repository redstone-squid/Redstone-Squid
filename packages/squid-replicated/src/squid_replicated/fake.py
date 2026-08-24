"""Deterministic operation-log backend for counter and tagged-set conformance tests."""

import json
from dataclasses import dataclass
from typing import Any

_MAX_UPDATE_BYTES = 1_048_576
_MAX_OPERATIONS = 10_000


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
class FakeOperation:
    identity: OperationId
    kind: str
    path: str
    value: str | int
    tags: tuple[OperationId, ...] = ()


@dataclass(frozen=True, slots=True)
class FakeVersion:
    operations: frozenset[OperationId]


@dataclass(frozen=True, slots=True)
class FakeSnapshot:
    counters: tuple[tuple[str, int], ...]
    sets: tuple[tuple[str, frozenset[str]], ...]

    def counter(self, path: str) -> int:
        return dict(self.counters).get(path, 0)

    def tagged_set(self, path: str) -> frozenset[str]:
        return dict(self.sets).get(path, frozenset())


@dataclass(frozen=True, slots=True)
class PreparedFakeUpdate:
    base: FakeVersion | None
    operations: tuple[FakeOperation, ...]


@dataclass(frozen=True, slots=True)
class FakeChange:
    operations: tuple[FakeOperation, ...]


class FakeBranch:
    def __init__(self, engine: FakeEngine) -> None:
        self._engine = engine
        self._base = engine.version()
        self._operations: list[FakeOperation] = []

    @property
    def base(self) -> FakeVersion:
        return self._base

    @property
    def operations(self) -> tuple[FakeOperation, ...]:
        return tuple(self._operations)

    def apply(self, operation: FakeOperation) -> None:
        self._operations.append(operation)

    def snapshot(self) -> FakeSnapshot:
        return self._engine.snapshot_with(self._operations)

    def prepare(self, base: object) -> PreparedFakeUpdate:
        assert isinstance(base, FakeVersion)
        return PreparedFakeUpdate(base, tuple(self._operations))


class FakeEngine:
    """A deterministic convergent operation set used to prove Squid integration semantics."""

    backend_id = "squid-fake-v1"

    def __init__(self, replica_id: str) -> None:
        self.replica_id = replica_id
        self._next_sequence = 0
        self._operations: dict[OperationId, FakeOperation] = {}

    def operation(self, kind: str, path: str, value: str | int, tags: tuple[OperationId, ...] = ()) -> FakeOperation:
        self._next_sequence += 1
        return FakeOperation(OperationId(self.replica_id, self._next_sequence), kind, path, value, tags)

    def version(self) -> FakeVersion:
        return FakeVersion(frozenset(self._operations))

    def branch(self) -> FakeBranch:
        return FakeBranch(self)

    def snapshot(self) -> FakeSnapshot:
        return self.snapshot_with(())

    def snapshot_with(self, additions: tuple[FakeOperation, ...] | list[FakeOperation]) -> FakeSnapshot:
        operations = {**self._operations, **{operation.identity: operation for operation in additions}}
        counters: dict[str, int] = {}
        added: dict[tuple[str, str], set[OperationId]] = {}
        removed: set[OperationId] = set()
        for operation in sorted(operations.values(), key=lambda item: item.identity):
            if operation.kind == "increment":
                assert isinstance(operation.value, int)
                counters[operation.path] = counters.get(operation.path, 0) + operation.value
            elif operation.kind == "add":
                assert isinstance(operation.value, str)
                added.setdefault((operation.path, operation.value), set()).add(operation.identity)
            elif operation.kind == "remove":
                removed.update(operation.tags)
        sets: dict[str, set[str]] = {}
        for (path, value), tags in added.items():
            if tags - removed:
                sets.setdefault(path, set()).add(value)
        return FakeSnapshot(
            tuple(sorted(counters.items())),
            tuple((path, frozenset(values)) for path, values in sorted(sets.items())),
        )

    def visible_tags(self, path: str, value: str, additions: tuple[FakeOperation, ...] = ()) -> tuple[OperationId, ...]:
        operations = {**self._operations, **{operation.identity: operation for operation in additions}}
        removed = {tag for operation in operations.values() if operation.kind == "remove" for tag in operation.tags}
        return tuple(
            operation.identity
            for operation in operations.values()
            if operation.kind == "add"
            and operation.path == path
            and operation.value == value
            and operation.identity not in removed
        )

    def apply(self, prepared: PreparedFakeUpdate) -> FakeChange:
        for operation in prepared.operations:
            self._operations.setdefault(operation.identity, operation)
        return FakeChange(prepared.operations)

    def prepare_remote(self, update: bytes) -> PreparedFakeUpdate:
        operations = self._decode_envelope(update, kind="update")
        return PreparedFakeUpdate(None, operations)

    def encode_token(self, operations: tuple[FakeOperation, ...]) -> bytes:
        """Encode one action's opaque change token for durable history tests."""
        return self._encode_envelope("history-token", operations)

    def decode_token(self, token: bytes) -> tuple[FakeOperation, ...]:
        """Decode a schema-checked action token without applying it."""
        return self._decode_envelope(token, kind="history-token")

    def export_since(self, version: object | None = None) -> bytes:
        known = version.operations if isinstance(version, FakeVersion) else frozenset()
        operations = tuple(
            operation for identity, operation in sorted(self._operations.items()) if identity not in known
        )
        return self._encode_envelope("update", operations)

    def _decode_envelope(self, data: bytes, *, kind: str) -> tuple[FakeOperation, ...]:
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
        operations: list[FakeOperation] = []
        identities: dict[OperationId, FakeOperation] = {}
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

    def _decode_operation(self, item: dict[str, Any]) -> FakeOperation:
        identity = OperationId.decode(item["id"])
        kind = item["kind"]
        path = item["path"]
        value = item["value"]
        tags = item.get("tags", ())
        if kind not in {"increment", "add", "remove"} or not isinstance(path, str) or not isinstance(tags, list):
            message = "replicated operation has invalid fields"
            raise ValueError(message)
        if (kind == "increment" and not isinstance(value, int)) or (
            kind in {"add", "remove"} and not isinstance(value, str)
        ):
            message = "replicated operation value does not match its kind"
            raise ValueError(message)
        return FakeOperation(identity, kind, path, value, tuple(OperationId.decode(tag) for tag in tags))

    def _encode_envelope(self, kind: str, operations: tuple[FakeOperation, ...]) -> bytes:
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
                }
                for operation in operations
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

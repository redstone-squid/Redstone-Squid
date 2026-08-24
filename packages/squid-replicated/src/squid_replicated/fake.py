"""Deterministic operation-log backend for counter and tagged-set conformance tests."""

import json
from dataclasses import dataclass


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
        payload = json.loads(update)
        if payload.get("backend") != self.backend_id or payload.get("schema") != 1:
            message = "replicated update has the wrong backend or schema"
            raise ValueError(message)
        operations = tuple(
            FakeOperation(
                OperationId.decode(item["id"]),
                item["kind"],
                item["path"],
                item["value"],
                tuple(OperationId.decode(tag) for tag in item.get("tags", ())),
            )
            for item in payload["operations"]
        )
        return PreparedFakeUpdate(None, operations)

    def export_since(self, version: object | None = None) -> bytes:
        known = version.operations if isinstance(version, FakeVersion) else frozenset()
        operations = [operation for identity, operation in sorted(self._operations.items()) if identity not in known]
        payload = {
            "backend": self.backend_id,
            "schema": 1,
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

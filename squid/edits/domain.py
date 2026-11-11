"""Immutable commands, version stamps, and receipts shared by every editor."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import JsonValue
from whenever import Instant

from squid.core.errors import ConflictError, InvalidStateError
from squid.core.i18n import tr


@dataclass(frozen=True, slots=True, order=True)
class EditTarget:
    kind: str
    key: str

    @property
    def identity(self) -> str:
        return f"{self.kind}:{self.key}"


@dataclass(frozen=True, slots=True)
class Revision:
    target: EditTarget
    value: int


@dataclass(frozen=True, slots=True)
class EditValues:
    """A JSON value projection with no mutable objects shared with the caller."""

    encoded: str

    def __post_init__(self) -> None:
        values = json.loads(self.encoded)
        if not isinstance(values, dict):
            message = "Edit values must be a JSON object"
            raise TypeError(message)
        object.__setattr__(self, "encoded", json.dumps(values, sort_keys=True, separators=(",", ":"), allow_nan=False))

    @classmethod
    def of(cls, values: Mapping[str, JsonValue]) -> Self:
        return cls(json.dumps(dict(values), sort_keys=True, separators=(",", ":"), allow_nan=False))

    def unpack(self) -> dict[str, JsonValue]:
        return json.loads(self.encoded)

    def patch(self, changes: EditValues) -> EditValues:
        return self.of(self.unpack() | changes.unpack())


@dataclass(frozen=True, slots=True)
class EditCommand:
    kind: str
    expected: tuple[Revision, ...]
    values: EditValues
    operation_id: UUID = field(default_factory=uuid4)
    purpose: Literal["edit", "undo", "redo"] = "edit"
    cause: UUID | None = None
    issued_at: Instant = field(default_factory=Instant.now)

    def __post_init__(self) -> None:
        targets = [version.target for version in self.expected]
        if not targets or len(set(targets)) != len(targets):
            message = "An edit requires distinct versioned targets"
            raise ValueError(message)
        if any(version.value < 0 for version in self.expected):
            message = "An expected revision cannot be negative"
            raise ValueError(message)

    @property
    def fingerprint(self) -> bytes:
        """Hash edit meaning, excluding the retry's transport-specific issue time."""
        content = json.dumps(
            [
                self.kind,
                [(v.target.kind, v.target.key, v.value) for v in sorted(self.expected, key=lambda v: v.target)],
                self.values.encoded,
                self.purpose,
                str(self.cause),
            ],
            separators=(",", ":"),
        )
        return hashlib.sha256(content.encode()).digest()


@dataclass(frozen=True, slots=True)
class PreparedEdit:
    """Validated domain projections, captured before any write."""

    before: EditValues
    after: EditValues
    final: bool = False
    resources: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.before != self.after


@dataclass(frozen=True, slots=True)
class EditReceipt:
    command: EditCommand
    before: EditValues
    after: EditValues
    revisions: tuple[Revision, ...]
    final: bool = False
    resources: tuple[str, ...] = ()

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(version.target.identity for version in self.revisions)

    def inverse(self, expected: tuple[Revision, ...], *, redo: bool = False) -> EditCommand:
        if self.final:
            raise InvalidStateError(tr(t"edits-final = This operation cannot be undone."))
        return EditCommand(
            self.command.kind,
            expected,
            self.before,
            purpose="redo" if redo else "undo",
            cause=self.command.operation_id,
        )


@dataclass(frozen=True, slots=True)
class EditOutcome:
    operation_id: UUID
    actor: str
    fingerprint: bytes
    revisions: tuple[Revision, ...]
    changed: bool
    final: bool


@dataclass(slots=True)
class EditAttempt:
    """Live-session recovery information; no inverse values are stored in the database."""

    receipt: EditReceipt | None = None


@dataclass(frozen=True, slots=True)
class EditResult:
    outcome: EditOutcome
    receipt: EditReceipt | None
    replayed: bool = False


class RevisionConflictError(ConflictError):
    default_message = tr(t"edits-revision-conflict = This item changed elsewhere. Reload it before editing.")

    def __init__(self, expected: Revision, current: int) -> None:
        super().__init__(context={"target": expected.target.identity, "expected": expected.value, "current": current})
        self.expected = expected
        self.current = current


class OperationReuseError(ConflictError):
    default_message = tr(t"edits-operation-reused = This operation identifier was already used for another edit.")


class EditCommitUncertainError(InvalidStateError):
    default_message = tr(
        t"edits-commit-uncertain = The saved result needs checking before this item can be edited again."
    )

    def __init__(self, operation_id: UUID) -> None:
        super().__init__(context={"operation_id": str(operation_id)})
        self.operation_id = operation_id


class EditExpiredError(InvalidStateError):
    default_message = tr(t"edits-operation-expired = This edit has expired. Reload the item before editing again.")


class ConcurrentEditError(ConflictError):
    default_message = tr(
        t"edits-concurrent-write = Another edit committed first. Reload this item before trying again."
    )

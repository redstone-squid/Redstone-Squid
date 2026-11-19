"""Edit commands, revisions, receipts, and the errors an edit can raise."""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import JsonValue
from whenever import Instant

from squid.core.errors import ConflictError, ErrorCode, InvalidStateError, ValidationError
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
    """A target's revision counter: the value a writer expects, or the value after a write."""

    target: EditTarget
    value: int


@dataclass(frozen=True, slots=True)
class LatestRevision:
    """Expect whatever revision the target has when the handler locks it.

    HTTP requests without If-Match use this to apply a change to the current state.
    """

    target: EditTarget
    value: None = field(default=None, init=False)


@dataclass(frozen=True, slots=True)
class EditValues:
    """A JSON value projection with no mutable objects shared with the caller."""

    encoded: str

    def __post_init__(self) -> None:
        values = json.loads(self.encoded)
        if not isinstance(values, dict):
            message = "Edit values must be a JSON object"
            raise ValidationError(message)
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
    """A requested change to one or more targets, named by the handler `kind`.

    `expected` lists every target the edit may change, with the revision the caller
    displayed or `LatestRevision`. The command is never rewritten during execution;
    the handler reports the revisions it found in `PreparedEdit.revisions`.
    `operation_id` identifies the edit across retries.
    """

    kind: str
    expected: tuple[Revision | LatestRevision, ...]
    values: EditValues
    operation_id: UUID = field(default_factory=uuid4)
    purpose: Literal["edit", "undo", "redo"] = "edit"
    cause: UUID | None = None
    issued_at: Instant = field(default_factory=Instant.now)

    def __post_init__(self) -> None:
        targets = [version.target for version in self.expected]
        if not targets or len(set(targets)) != len(targets):
            message = "An edit requires distinct versioned targets"
            raise ValidationError(message)
        if any(version.value is not None and version.value < 0 for version in self.expected):
            message = "An expected revision cannot be negative"
            raise ValidationError(message)

        if self.purpose != "edit" and any(isinstance(version, LatestRevision) for version in self.expected):
            message = "Undo and redo require explicit revisions"
            raise ValidationError(message)

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
    """A handler's validated view of an edit, captured before any write.

    Attributes:
        before: The current values of the fields the edit touches.
        after: The normalized values the edit will write.
        final: The edit cannot be undone, for example a deletion.
        revisions: The current revision of every target in the command, read under the
            handler's lock. `apply` advances these.
    """

    before: EditValues
    after: EditValues
    final: bool = False
    revisions: tuple[Revision, ...] = field(kw_only=True)

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

    @property
    def targets(self) -> frozenset[str]:
        return frozenset(version.target.identity for version in self.revisions)

    def undo(self, expected: tuple[Revision, ...]) -> EditCommand:
        """Return a command that writes the values from before this edit."""
        return self._replay(expected, self.before, "undo")

    def redo(self, expected: tuple[Revision, ...]) -> EditCommand:
        """Return a command that writes this edit's values again after an undo."""
        return self._replay(expected, self.after, "redo")

    def _replay(
        self, expected: tuple[Revision, ...], values: EditValues, purpose: Literal["undo", "redo"]
    ) -> EditCommand:
        if self.final:
            raise InvalidStateError(tr(t"edits-final = This operation cannot be undone."))
        return EditCommand(self.command.kind, expected, values, purpose=purpose, cause=self.command.operation_id)


@dataclass(frozen=True, slots=True)
class EditOutcome:
    operation_id: UUID
    actor: str
    fingerprint: bytes
    revisions: tuple[Revision, ...]
    changed: bool
    final: bool


@dataclass(frozen=True, slots=True)
class EditResult:
    """The outcome of an edit, with its receipt unless the command was a retry.

    A retry of a committed operation returns the stored outcome with `replayed` set.
    Its receipt is None, because the old values are not stored.
    """

    outcome: EditOutcome
    receipt: EditReceipt | None
    replayed: bool = False


class RevisionConflictError(ConflictError):
    default_message = tr(t"edits-revision-conflict = This item changed elsewhere. Reload it before editing.")

    def __init__(self, expected: Revision, current: int) -> None:
        super().__init__(context={"target": expected.target.identity, "expected": expected.value, "current": current})
        self.expected = expected
        self.current = current


def require_revision(expected: Revision | LatestRevision, current: int) -> Revision:
    """Return the target's current revision, or raise if a concrete expectation differs from it."""
    if isinstance(expected, Revision) and expected.value != current:
        raise RevisionConflictError(expected, current)
    return Revision(expected.target, current)


class OperationReuseError(ConflictError):
    default_code = ErrorCode.IDEMPOTENCY_CONFLICT

    default_message = tr(t"edits-operation-reused = This operation identifier was already used for another edit.")


class EditCommitUncertainError(InvalidStateError):
    """The commit failed in a way that may or may not have saved the edit.

    Retrying the same command is safe: it returns the stored outcome if the first
    attempt committed, and applies the edit otherwise.
    """

    default_message = tr(
        t"edits-commit-uncertain = The change may not have been saved. Check the values and try again."
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

"""Run an edit command through its handler in one transaction.

The executor also stores each operation's outcome, so a retried command returns the
first result instead of applying the change twice.
"""

import logging
from collections.abc import Mapping
from typing import Protocol, runtime_checkable
from uuid import UUID

from anyio import CancelScope
from pydantic import ValidationError as SchemaValidationError
from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import ConflictError, InvalidStateError, ValidationError
from squid.core.replay import REPLAY_RETENTION_HOURS
from squid.edits.domain import (
    EditCommand,
    EditCommitUncertainError,
    EditExpiredError,
    EditOutcome,
    EditReceipt,
    EditResult,
    OperationReuseError,
    PreparedEdit,
    Revision,
    RevisionConflictError,
)
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


@runtime_checkable
class EditRunner(Protocol):
    async def execute(self, command: EditCommand, actor: Actor) -> EditResult: ...


class EditStore(Protocol):
    """Operation locks last until the transaction ends, so a retry waits for the original."""

    async def lock_operation(self, operation_id: UUID) -> None: ...

    async def outcome(self, operation_id: UUID) -> EditOutcome | None: ...

    async def record(self, outcome: EditOutcome) -> None: ...


class EditUnitOfWork(UnitOfWork, Protocol):
    @property
    def edits(self) -> EditStore: ...


class EditHandler[UowT: EditUnitOfWork](Protocol):
    """Domain behavior for one kind of edit, used for ordinary edits, undo, and redo.

    `prepare` locks the targets in a stable order, checks permissions, and validates
    and normalizes the requested values. It reports the current revision of every
    guarded target in `PreparedEdit.revisions` and raises `RevisionConflictError` if
    one differs from the command's expectation. Its `after` values must be the
    normalized values `apply` will write, not the caller's raw input.

    `apply` runs only if the values changed. It writes them, flushes, and returns each
    target's revision after the write; it never commits.
    """

    async def prepare(self, uow: UowT, actor: Actor, command: EditCommand) -> PreparedEdit: ...

    async def apply(
        self,
        uow: UowT,
        actor: Actor,
        command: EditCommand,
        prepared: PreparedEdit,
    ) -> tuple[Revision, ...]: ...


class EditExecutor[UowT: EditUnitOfWork]:
    """Execute explicit domain handlers without depending on a transport or UI runtime."""

    def __init__(self, factory: UnitOfWorkFactory[UowT], handlers: Mapping[str, EditHandler[UowT]]) -> None:
        self._factory = factory
        self._handlers = dict(handlers)

    async def execute(self, command: EditCommand, actor: Actor) -> EditResult:
        confirmed: EditResult | None = None
        try:
            async with self._factory() as uow:
                confirmed = await self._execute_in(uow, command, actor)
        except Exception:
            if confirmed is None:
                raise
            # Session cleanup cannot undo an acknowledged commit. Return its
            # receipt so transports retain history and do not report a failed save.
            logger.warning("Edit committed but transaction cleanup failed", exc_info=True)
        return confirmed

    async def _execute_in(self, uow: UowT, command: EditCommand, actor: Actor) -> EditResult:
        actor_key = actor.key
        handler = self._handlers[command.kind]
        await uow.edits.lock_operation(command.operation_id)
        existing = await uow.edits.outcome(command.operation_id)
        if existing is not None:
            self._check_replay(existing, command, actor_key)
            return EditResult(existing, None, replayed=True)
        self._check_deadline(command)
        try:
            prepared = await handler.prepare(uow, actor, command)
        except SchemaValidationError as error:
            raise ValidationError(str(error)) from error
        base = self._current_revisions(command, prepared.revisions)
        versions = (await handler.apply(uow, actor, command, prepared)) if prepared.changed else base
        if len(versions) != len(command.expected) or {v.target for v in versions} != {
            v.target for v in command.expected
        }:
            message = "An edit must return every guarded target's resulting revision"
            raise InvalidStateError(message)
        expected = {v.target: v.value for v in base}
        if any(v.value < expected[v.target] for v in versions) or (
            prepared.changed and not any(v.value > expected[v.target] for v in versions)
        ):
            message = "Changed edits must advance revisions; revisions must never decrease"
            raise InvalidStateError(message)
        receipt = EditReceipt(command, prepared.before, prepared.after, versions, prepared.final)
        outcome = EditOutcome(
            command.operation_id, actor_key, command.fingerprint, versions, prepared.changed, prepared.final
        )
        await uow.edits.record(outcome)
        # Defer caller cancellation while commit settles. Once confirmed, the
        # caller can accept the receipt synchronously before its next await.
        try:
            with CancelScope(shield=True):
                await uow.commit()
        except ConflictError:
            raise
        except Exception as error:
            raise EditCommitUncertainError(command.operation_id) from error
        return EditResult(outcome, receipt)

    @staticmethod
    def _current_revisions(command: EditCommand, revisions: tuple[Revision, ...]) -> tuple[Revision, ...]:
        """Check the handler's reported revisions against the command's expectations."""
        current = {version.target: version.value for version in revisions}
        if len(current) != len(revisions) or current.keys() != {version.target for version in command.expected}:
            message = "prepare must report the current revision of every guarded target"
            raise InvalidStateError(message)
        for version in command.expected:
            if isinstance(version, Revision) and current[version.target] != version.value:
                raise RevisionConflictError(version, current[version.target])
        return revisions

    @staticmethod
    def _check_deadline(command: EditCommand) -> None:
        if command.issued_at.add(hours=REPLAY_RETENTION_HOURS) <= Instant.now():
            raise EditExpiredError

    @staticmethod
    def _check_replay(existing: EditOutcome, command: EditCommand, actor: str) -> None:
        if existing.actor != actor or existing.fingerprint != command.fingerprint:
            raise OperationReuseError

"""One transaction for validation, conditional mutation, and an outcome record."""

import logging
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from anyio import CancelScope
from pydantic import ValidationError as SchemaValidationError
from whenever import Instant

from squid.core.errors import ConflictError, ValidationError
from squid.core.i18n import tr
from squid.edits.domain import (
    EditActor,
    EditAttempt,
    EditCommand,
    EditCommitUncertainError,
    EditExpiredError,
    EditOutcome,
    EditReceipt,
    EditResult,
    EditValues,
    OperationReuseError,
    PreparedEdit,
    Revision,
)
from squid.permissions.domain import Subject
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

logger = logging.getLogger(__name__)


class EditRunner(Protocol):
    async def execute(
        self, command: EditCommand, actor: EditActor, *, attempt: EditAttempt | None = None
    ) -> EditResult: ...

    async def reconcile(self, command: EditCommand, actor: EditActor, attempt: EditAttempt) -> EditResult | None: ...


class EditStore(Protocol):
    """Operation locks live until transaction completion, including during reconciliation."""

    async def lock_operation(self, operation_id: UUID) -> None: ...

    async def outcome(self, operation_id: UUID) -> EditOutcome | None: ...

    async def record(self, outcome: EditOutcome) -> None: ...


class EditUnitOfWork(UnitOfWork, Protocol):
    @property
    def edits(self) -> EditStore: ...


class EditHandler[UowT: EditUnitOfWork](Protocol):
    """Domain behavior shared by ordinary edits and their reversals.

    Lock targets in stable order and check every expected revision in prepare.
    Authorization and normalization belong there too. Apply advances the revisions
    and flushes the writes, but never commits. Final projections must describe the
    normalized values actually written, rather than the user's unvalidated input.
    """

    async def prepare(self, uow: UowT, actor: Subject, command: EditCommand) -> PreparedEdit: ...

    async def apply(
        self,
        uow: UowT,
        actor: Subject,
        command: EditCommand,
        prepared: PreparedEdit,
    ) -> tuple[Revision, ...]: ...


class FieldEditHandler[UowT: EditUnitOfWork]:
    """Generate inverse values for an explicitly declared set of editable fields.

    Domain adapters implement loading, validation, authorization, and persistence
    once. Screens only supply the command. Load must lock and check the revision;
    validate may normalize values and must authorize the current actor in this UOW.
    """

    fields: frozenset[str]

    async def load(self, uow: UowT, command: EditCommand) -> EditValues:
        raise NotImplementedError

    async def validate(
        self,
        uow: UowT,
        actor: Subject,
        command: EditCommand,
        values: EditValues,
    ) -> EditValues:
        raise NotImplementedError

    async def prepare(self, uow: UowT, actor: Subject, command: EditCommand) -> PreparedEdit:
        changes = command.values.unpack()
        if not changes.keys() <= self.fields:
            raise ValidationError(tr(t"edits-fields-not-editable = This edit contains fields that cannot be changed."))
        before = await self.load(uow, command)
        after = await self.validate(uow, actor, command, before.patch(command.values))
        if before.unpack().keys() != after.unpack().keys() or not after.unpack().keys() <= self.fields:
            message = "A field edit must preserve its declared projection"
            raise ValueError(message)
        # Retain the complete declared projection. Strict aggregate revisions fence
        # every reversal, so restoring it cannot overwrite an unrelated later edit.
        return PreparedEdit(before, after)

    async def apply(
        self,
        uow: UowT,
        actor: Subject,
        command: EditCommand,
        prepared: PreparedEdit,
    ) -> tuple[Revision, ...]:
        raise NotImplementedError


class EditExecutor[UowT: EditUnitOfWork]:
    """Execute explicit domain handlers without depending on a transport or UI runtime."""

    def __init__(self, factory: UnitOfWorkFactory[UowT], handlers: Mapping[str, EditHandler[UowT]]) -> None:
        self._factory = factory
        self._handlers = dict(handlers)

    async def execute(
        self,
        command: EditCommand,
        actor: EditActor,
        *,
        attempt: EditAttempt | None = None,
    ) -> EditResult:
        confirmed: EditResult | None = None
        try:
            async with self._factory() as uow:
                confirmed = await self._execute_in(uow, command, actor, attempt)
        except Exception:
            if confirmed is None:
                raise
            # Session cleanup cannot undo an acknowledged commit. Return its
            # receipt so transports retain history and do not report a failed save.
            logger.warning("Edit committed but transaction cleanup failed", exc_info=True)
        return confirmed

    async def _execute_in(
        self, uow: UowT, command: EditCommand, actor: EditActor, attempt: EditAttempt | None
    ) -> EditResult:
        actor_key = actor.key
        handler = self._handlers[command.kind]
        await uow.edits.lock_operation(command.operation_id)
        existing = await uow.edits.outcome(command.operation_id)
        if existing is not None:
            self._check_replay(existing, command, actor_key)
            receipt = self._receipt(attempt, command, existing)
            return EditResult(existing, receipt, replayed=True)
        self._check_deadline(command)
        try:
            prepared = await handler.prepare(uow, actor.subject, command)
        except SchemaValidationError as error:
            raise ValidationError(str(error)) from error
        versions = (
            (await handler.apply(uow, actor.subject, command, prepared)) if prepared.changed else command.expected
        )
        if len(versions) != len(command.expected) or {v.target for v in versions} != {
            v.target for v in command.expected
        }:
            message = "An edit must return every guarded target's resulting revision"
            raise ValueError(message)
        expected = {v.target: v.value for v in command.expected}
        if any(v.value < expected[v.target] for v in versions) or (
            prepared.changed and not any(v.value > expected[v.target] for v in versions)
        ):
            message = "Changed edits must advance revisions; revisions must never decrease"
            raise ValueError(message)
        receipt = EditReceipt(command, prepared.before, prepared.after, versions, prepared.final, prepared.resources)
        outcome = EditOutcome(
            command.operation_id, actor_key, command.fingerprint, versions, prepared.changed, prepared.final
        )
        await uow.edits.record(outcome)
        if attempt is not None:
            attempt.receipt = receipt
        # Defer caller cancellation while commit settles. Once confirmed, the
        # caller can accept the receipt synchronously before its next await.
        try:
            with CancelScope(shield=True):
                await uow.commit()
        except ConflictError:
            if attempt is not None:
                attempt.receipt = None
            raise
        except Exception as error:
            raise EditCommitUncertainError(command.operation_id) from error
        return EditResult(outcome, receipt)

    async def reconcile(self, command: EditCommand, actor: EditActor, attempt: EditAttempt) -> EditResult | None:
        """Return the confirmed result, or None only after proving the original transaction ended without it."""
        async with self._factory() as uow:
            await uow.edits.lock_operation(command.operation_id)
            existing = await uow.edits.outcome(command.operation_id)
            if existing is None:
                self._check_deadline(command)
                return None
            self._check_replay(existing, command, actor.key)
            return EditResult(existing, self._receipt(attempt, command, existing), replayed=True)

    @staticmethod
    def _check_deadline(command: EditCommand) -> None:
        if command.issued_at.add(hours=24) <= Instant.now():
            raise EditExpiredError

    @staticmethod
    def _receipt(attempt: EditAttempt | None, command: EditCommand, outcome: EditOutcome) -> EditReceipt | None:
        receipt = None if attempt is None else attempt.receipt
        if receipt is not None and (
            receipt.command.operation_id != command.operation_id
            or receipt.command.fingerprint != command.fingerprint
            or receipt.revisions != outcome.revisions
        ):
            raise OperationReuseError
        return receipt

    @staticmethod
    def _check_replay(existing: EditOutcome, command: EditCommand, actor: str) -> None:
        if existing.actor != actor or existing.fingerprint != command.fingerprint:
            raise OperationReuseError

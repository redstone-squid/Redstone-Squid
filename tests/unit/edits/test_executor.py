"""Application edit semantics, with transaction rollback and commit ambiguity."""

from dataclasses import dataclass, field, replace
from functools import partial
from types import TracebackType
from uuid import UUID

import pytest
from pydantic import BaseModel, ConfigDict, JsonValue
from whenever import Instant

from squid.core.actors import Actor
from squid.core.errors import AuthorizationError, ValidationError
from squid.edits.application import EditExecutor
from squid.edits.domain import (
    EditAttempt,
    EditCommand,
    EditCommitUncertainError,
    EditExpiredError,
    EditOutcome,
    EditTarget,
    EditValues,
    OperationReuseError,
    Revision,
    RevisionConflictError,
)
from squid.edits.fields import Aggregate, FieldCommand
from tests.support.unit_of_work import CommittedState, FakeUnitOfWork

TARGET = EditTarget("example", "1")
ACTOR = Actor(account_id=7)


@dataclass
class Store:
    values: EditValues = field(default_factory=lambda: EditValues.of({"name": "Original", "enabled": True}))
    revision: int = 1
    allowed: bool = True
    outcomes: dict[UUID, EditOutcome] = field(default_factory=dict)

    async def lock_operation(self, operation_id: UUID) -> None:
        pass

    async def outcome(self, operation_id: UUID) -> EditOutcome | None:
        return self.outcomes.get(operation_id)

    async def record(self, outcome: EditOutcome) -> None:
        self.outcomes[outcome.operation_id] = outcome


class Work(FakeUnitOfWork[Store]):
    @property
    def edits(self) -> Store:
        return self.state


class Values(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str
    enabled: bool


class Handler(FieldCommand[Work, Store, Values]):
    def __init__(self) -> None:
        async def load(uow: Work, command: EditCommand) -> Store:
            return uow.state

        async def flush(uow: Work, root: Store, expected: Revision) -> Revision:
            root.revision += 1
            return Revision(TARGET, root.revision)

        async def authorize(uow: Work, actor: Actor, root: Store) -> None:
            if not root.allowed or actor.account_id != 7:
                raise AuthorizationError

        super().__init__(Aggregate(TARGET, load, lambda root: root.revision, flush), Values, authorize)

    async def capture(self, uow: Work, root: Store, command: EditCommand) -> Values:
        return Values.model_validate(root.values.unpack())

    async def write(self, uow: Work, root: Store, before: Values, after: Values) -> None:
        root.values = EditValues.of(after.model_dump(mode="json"))


def command(name: str = "Changed", revision: int = 1) -> EditCommand:
    return EditCommand("example", (Revision(TARGET, revision),), EditValues.of({"name": name}))


async def test_normalized_edit_replay_and_inverse_use_the_same_handler() -> None:
    state = CommittedState(Store())
    executor = EditExecutor(partial(Work, state), {"example": Handler()})
    edit = command("  Changed  ")
    attempt = EditAttempt()
    result = await executor.execute(edit, ACTOR, attempt=attempt)
    assert result.receipt is not None
    assert result.receipt.after.unpack()["name"] == "Changed"
    assert result.receipt.revisions == (Revision(TARGET, 2),)
    replay = await executor.execute(edit, ACTOR, attempt=attempt)
    assert replay.replayed
    assert replay.receipt == result.receipt
    assert state.value.revision == 2
    restored = await executor.execute(result.receipt.inverse(result.receipt.revisions), ACTOR)
    assert restored.receipt is not None
    assert state.value.values.unpack()["name"] == "Original"
    assert state.value.revision == 3
    await executor.execute(restored.receipt.inverse(restored.receipt.revisions, redo=True), ACTOR)
    assert state.value.values.unpack()["name"] == "Changed"
    assert state.value.revision == 4


async def test_noops_authorization_unknown_fields_and_stale_versions() -> None:
    state = CommittedState(Store())
    executor = EditExecutor(partial(Work, state), {"example": Handler()})
    assert not (await executor.execute(command("Original"), ACTOR)).outcome.changed
    assert state.value.revision == 1
    with pytest.raises(ValidationError):
        await executor.execute(replace(command(), values=EditValues.of({"password": "secret"})), ACTOR)
    state.value.allowed = False
    with pytest.raises(AuthorizationError):
        await executor.execute(command(), ACTOR)
    state.value.allowed = True
    state.value.revision = 3  # Another writer changed a value away and back.
    with pytest.raises(RevisionConflictError):
        await executor.execute(command(), ACTOR)
    assert state.value.values.unpack()["name"] == "Original"
    assert len(state.value.outcomes) == 1


@pytest.mark.parametrize("committed", [False, True])
async def test_uncertain_commit_is_reconciled_from_the_same_transaction_outcome(committed: bool) -> None:
    class FailingWork(Work):
        async def commit(self) -> None:
            if committed:
                await super().commit()
            raise ConnectionError("lost commit acknowledgement")

    state = CommittedState(Store())
    failing = EditExecutor(partial(FailingWork, state), {"example": Handler()})
    edit = command()
    attempt = EditAttempt()
    with pytest.raises(EditCommitUncertainError):
        await failing.execute(edit, ACTOR, attempt=attempt)
    executor = EditExecutor(partial(Work, state), {"example": Handler()})
    recovered = await executor.reconcile(edit, ACTOR, attempt)
    assert (recovered is not None) == committed
    if not committed:
        await executor.execute(edit, ACTOR, attempt=attempt)
    assert state.value.revision == 2
    assert len(state.value.outcomes) == 1


async def test_operation_reuse_and_expired_attempts_cannot_write() -> None:
    state = CommittedState(Store())
    executor = EditExecutor(partial(Work, state), {"example": Handler()})
    edit = command()
    await executor.execute(edit, ACTOR)
    with pytest.raises(OperationReuseError):
        await executor.execute(replace(edit, values=EditValues.of({"name": "Other"})), ACTOR)
    with pytest.raises(OperationReuseError):
        await executor.execute(edit, Actor(account_id=8))
    expired = replace(command(revision=2), issued_at=Instant.now().subtract(hours=25))
    with pytest.raises(EditExpiredError):
        await executor.execute(expired, ACTOR)
    with pytest.raises(EditExpiredError):
        await executor.reconcile(expired, ACTOR, EditAttempt())
    assert state.value.revision == 2


def test_value_projection_has_no_mutable_aliases() -> None:
    items: list[JsonValue] = ["before"]
    values: dict[str, JsonValue] = {"links": items}
    snapshot = EditValues.of(values)
    items.append("after")
    decoded = snapshot.unpack()
    decoded["links"] = []
    assert snapshot.unpack() == {"links": ["before"]}


async def test_acknowledged_commit_survives_transaction_cleanup_failure() -> None:
    class CloseFailure(Work):
        async def __aexit__(
            self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
        ) -> None:
            raise ConnectionError("session close failed")

    state = CommittedState(Store())
    executor = EditExecutor(partial(CloseFailure, state), {"example": Handler()})
    edit = command()
    result = await executor.execute(edit, ACTOR)
    assert result.receipt is not None
    assert result.outcome.revisions == (Revision(TARGET, 2),)
    replay = await executor.execute(edit, ACTOR)
    assert replay.replayed
    assert state.value.revision == 2

"""Both action modes share terminal-state and cleanup guarantees."""

import asyncio
from contextvars import Context, copy_context
from typing import override

import pytest

from squid_reactivity import (
    ActionCommit,
    ActionResult,
    ActionRollback,
    FrameworkIntegrityError,
    Reactive,
    StaleReactiveContextError,
    readonly_transaction,
    state,
    transaction,
)
from squid_reactivity.testing import InterleavingHarness


@pytest.mark.parametrize("readonly", [False, True])
@pytest.mark.parametrize("checkpoint", ["transaction.enter_body", "commit.before_validation"])
def test_cancellation_before_commit_clears_the_transaction_context(readonly: bool, checkpoint: str) -> None:
    schedule = InterleavingHarness()

    def cancel() -> None:
        raise asyncio.CancelledError

    schedule.at(checkpoint, cancel)
    scope = readonly_transaction if readonly else transaction
    with schedule.installed(), pytest.raises(asyncio.CancelledError), scope():
        pass
    # A failed entry must also clear its context, allowing an independent action.
    with transaction() as recovery:
        pass
    assert isinstance(recovery.result, ActionCommit)


@pytest.mark.parametrize("readonly", [False, True])
def test_cancellation_during_notifications_preserves_the_commit_result(readonly: bool) -> None:
    schedule = InterleavingHarness()
    seen: list[ActionResult] = []

    def cancel() -> None:
        raise asyncio.CancelledError

    schedule.at("commit.gate_released", cancel)
    scope = readonly_transaction if readonly else transaction
    with schedule.installed(), pytest.raises(asyncio.CancelledError), scope() as tx:
        tx.after_result(seen.append)
    assert isinstance(tx.result, ActionCommit)
    assert seen == [tx.result]


@pytest.mark.parametrize("readonly", [False, True])
def test_failure_after_publication_is_a_damaged_commit_in_either_mode(readonly: bool) -> None:
    schedule = InterleavingHarness()

    def fail() -> None:
        raise RuntimeError("publication failed")

    schedule.at("commit.after_cell_publication", fail)
    scope = readonly_transaction if readonly else transaction
    with schedule.installed(), pytest.raises(FrameworkIntegrityError), scope() as tx:
        pass
    assert isinstance(tx.result, ActionCommit)
    assert "framework_integrity_failure" in tx.result.tags


@pytest.mark.parametrize("readonly", [False, True])
def test_body_failure_exposes_rollback_and_delivers_callbacks_once(readonly: bool) -> None:
    seen: list[ActionResult] = []
    scope = readonly_transaction if readonly else transaction
    with pytest.raises(ValueError, match="body failed"), scope() as tx:
        tx.after_result(seen.append)
        raise ValueError("body failed")
    assert isinstance(tx.result, ActionRollback)
    assert seen == [tx.result]


def test_inherited_transaction_is_closed_before_owner_notifications() -> None:
    inherited = Context()

    class Model(Reactive):
        value: int = state(0)

        def on_state_commit(self, names: frozenset[str]) -> None:
            with pytest.raises(StaleReactiveContextError):
                inherited.run(setattr, self, "value", 2)

    model = Model()
    with transaction():
        model.value = 1
        inherited = copy_context()
    assert model.value == 1


def test_owner_notifications_group_fields_by_identity_and_preserve_order() -> None:
    commits: list[tuple[str, frozenset[str]]] = []
    rollbacks: list[str] = []

    class Model(Reactive):
        first: int = state(0)
        second: int = state(0)

        def __init__(self, label: str) -> None:
            self.label = label

        @override
        def __eq__(self, other: object) -> bool:
            # Equal, unhashable owners must still receive separate notifications.
            return isinstance(other, Model)

        @override
        def on_state_commit(self, names: frozenset[str]) -> None:
            commits.append((self.label, names))

        @override
        def on_state_rollback(self) -> None:
            rollbacks.append(self.label)

    left, right = Model("left"), Model("right")
    assert left == right
    with pytest.raises(TypeError):
        hash(left)

    with transaction():
        right.first = 1
        left.second = 2
        right.second = 3
        right.first = 4
    assert commits == [
        ("right", frozenset({"__state_first", "__state_second"})),
        ("left", frozenset({"__state_second"})),
    ]
    assert not rollbacks

    with pytest.raises(ValueError, match="discard"), transaction():
        right.first = 5
        left.second = 6
        right.second = 7
        right.first = 8
        raise ValueError("discard")
    assert rollbacks == ["left", "right"]
    assert len(commits) == 2
    assert (right.first, right.second, left.second) == (4, 3, 2)

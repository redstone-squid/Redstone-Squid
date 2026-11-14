"""Transaction outcomes remain accessible independently of diagnostic observers."""

from contextvars import copy_context
from typing import Literal

import pytest

from squid_reactivity import (
    ActorRef,
    FrameworkIntegrityError,
    Reactive,
    StaleReactiveContextError,
    TransactionCommit,
    TransactionContext,
    TransactionPurpose,
    TransactionRelation,
    TransactionResultSnapshot,
    TransactionRollback,
    apply_conditional_patches,
    current_transaction,
    inspect_cells,
    readonly_transaction,
    state,
    transaction,
)
from squid_reactivity.testing import InterleavingHarness


class Counter(Reactive):
    value: int = state(0)


def test_nested_blocks_share_the_result_and_callbacks_see_it() -> None:
    model = Counter()
    seen: list[TransactionCommit] = []
    with transaction("increment", actor=ActorRef("user", "42")) as tx:
        assert tx.result is None
        with transaction() as nested:
            assert nested is tx
            model.value += 1

        def committed(result: TransactionCommit) -> None:
            assert tx.result is result
            seen.append(result)

        tx.after_commit(committed)
    assert tx.result is seen[0]
    assert tx.context.name == "increment"
    assert tx.context.actor == ActorRef("user", "42")
    assert len(seen[0].patches) == 1
    with pytest.raises(StaleReactiveContextError):
        tx.after_commit(seen.append)


def test_rollback_result_survives_the_original_exception_and_can_start_recovery() -> None:
    model = Counter()
    seen: list[TransactionRollback] = []
    with pytest.raises(ValueError, match="failed"), transaction("edit") as tx:
        tx.after_rollback(seen.append)
        model.value = 3
        raise ValueError("failed")
    assert tx.result is seen[0]
    assert model.value == 0
    with transaction("recover", caused_by=tx.context, kind=TransactionPurpose.RECOVERY) as recovery:
        model.value = 4
    assert recovery.context.transaction_id != tx.context.transaction_id
    assert recovery.context.cause == tx.context.causal_ref()
    assert recovery.context.root_transaction_id == tx.context.root_transaction_id


def test_a_callback_can_open_a_new_identified_transaction() -> None:
    model = Counter()
    children = []

    def follow_up(result: TransactionCommit) -> None:
        with transaction(caused_by=result.context) as child:
            model.value = 2
        children.append(child)

    with transaction() as tx:
        model.value = 1
        tx.after_commit(follow_up)
    assert model.value == 2
    assert children[0].context.transaction_id != tx.context.transaction_id
    assert children[0].context.cause == tx.context.causal_ref()


def test_readonly_transactions_expose_their_result() -> None:
    with readonly_transaction() as tx:
        assert tx.result is None
    assert isinstance(tx.result, TransactionCommit)


def test_blind_write_records_the_value_it_replaces_and_can_be_undone() -> None:
    model = Counter()
    schedule = InterleavingHarness()
    schedule.at("transaction.close_staging", lambda: setattr(model, "value", 2))
    with schedule.installed(), transaction() as tx:
        model.value = 1
    assert isinstance(tx.result, TransactionCommit)
    patch = tx.result.patches.patches[0]
    assert patch.before.value == 2
    assert patch.before_version == 1
    assert patch.after_version == inspect_cells(model)["value"].version == 2

    with transaction():
        apply_conditional_patches(tx.result.patches.inverse())
    assert model.value == 2


def test_failure_after_cell_publication_preserves_the_commit() -> None:
    model = Counter()
    schedule = InterleavingHarness()

    def fail() -> None:
        raise RuntimeError("after publication")

    schedule.at("commit.after_cell_publication", fail)
    with schedule.installed(), pytest.raises(FrameworkIntegrityError), transaction() as tx:
        model.value = 1
    assert isinstance(tx.result, TransactionCommit)
    assert model.value == 1
    assert "framework_integrity_failure" in tx.result.tags


def test_rollback_notifies_each_owner_once_and_preserves_the_original_error() -> None:
    calls: list[str] = []

    class FailingOwner(Reactive):
        first: int = state(0)
        second: int = state(0)

        def on_state_rollback(self) -> None:
            calls.append("rollback")
            raise RuntimeError("cleanup failed")

    model = FailingOwner()
    with pytest.raises(ValueError, match="handler failed"), transaction() as tx:
        model.first = 1
        model.second = 2
        raise ValueError("handler failed")
    assert calls == ["rollback"]
    assert (model.first, model.second) == (0, 0)
    assert isinstance(tx.result, TransactionRollback)
    assert len(tx.result.cleanup_errors) == 1


@pytest.mark.parametrize("kind", ["reverses", "reapplies", "compensates"])
def test_history_relation_survives_snapshot_conversion(kind: Literal["reverses", "reapplies", "compensates"]) -> None:
    with transaction("original") as original:
        pass
    relation = TransactionRelation(kind, original.context.transaction_id)
    with transaction("history", relation=relation) as tx:
        pass
    assert isinstance(tx.result, TransactionCommit)
    snapshot = TransactionResultSnapshot.from_result(tx.result)
    assert snapshot.relation == relation


def test_result_hooks_reject_a_handle_from_another_transaction() -> None:
    with transaction("finished") as finished:
        pass
    with transaction("current") as current:
        with pytest.raises(StaleReactiveContextError):
            finished.after_commit(lambda result: None)
        assert not current.has_hook("finished")


def test_result_hook_keys_are_shared_across_outcomes() -> None:
    key = object()
    with transaction() as tx:
        tx.after_commit(lambda result: None, key=key)
        assert tx.has_hook(key)
        with pytest.raises(RuntimeError, match="already registered"):
            tx.after_rollback(lambda result: None, key=key)


def test_result_callbacks_keep_causality_without_an_active_transaction() -> None:
    recovered: list[TransactionContext] = []

    def recover(_commit: TransactionCommit) -> None:
        assert current_transaction() is None
        with transaction("recovery") as recovery:
            assert current_transaction() is recovery
            recovered.append(recovery.context)

    with transaction("original") as original:
        assert current_transaction() is original
        original.after_commit(recover)

    assert current_transaction() is None
    assert recovered[0].transaction_id != original.context.transaction_id
    assert recovered[0].cause == original.context.causal_ref()
    assert recovered[0].root_transaction_id == original.context.root_transaction_id


def test_copied_context_does_not_expose_a_finished_transaction_as_active() -> None:
    with transaction() as tx:
        inherited = copy_context()
        assert inherited.run(current_transaction) is tx
    assert inherited.run(current_transaction) is None

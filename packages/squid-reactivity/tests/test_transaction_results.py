"""Transaction outcomes remain accessible independently of diagnostic observers."""

import pytest

from squid_reactivity import (
    ActionCommit,
    ActionPurpose,
    ActionRollback,
    ActorRef,
    Reactive,
    StaleReactiveContextError,
    readonly_transaction,
    state,
    transaction,
)


class Counter(Reactive):
    value: int = state(0)


def test_nested_blocks_share_the_result_and_callbacks_see_it() -> None:
    model = Counter()
    seen: list[ActionCommit] = []
    with transaction("increment", actor=ActorRef("user", "42")) as tx:
        assert tx.result is None
        with transaction() as nested:
            assert nested is tx
            model.value += 1

        def committed(result: ActionCommit) -> None:
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
    seen: list[ActionRollback] = []
    with pytest.raises(ValueError, match="failed"), transaction("edit") as tx:
        tx.after_rollback(seen.append)
        model.value = 3
        raise ValueError("failed")
    assert tx.result is seen[0]
    assert model.value == 0
    with transaction("recover", caused_by=tx.context, kind=ActionPurpose.RECOVERY) as recovery:
        model.value = 4
    assert recovery.context.action_id != tx.context.action_id
    assert recovery.context.cause == tx.context.causal_ref()
    assert recovery.context.root_action_id == tx.context.root_action_id


def test_a_callback_can_open_a_new_identified_transaction() -> None:
    model = Counter()
    children = []

    def follow_up(result: ActionCommit) -> None:
        with transaction(caused_by=result.context) as child:
            model.value = 2
        children.append(child)

    with transaction() as tx:
        model.value = 1
        tx.after_commit(follow_up)
    assert model.value == 2
    assert children[0].context.action_id != tx.context.action_id
    assert children[0].context.cause == tx.context.causal_ref()


def test_readonly_transactions_expose_their_result() -> None:
    with readonly_transaction() as tx:
        assert tx.result is None
    assert isinstance(tx.result, ActionCommit)

"""Adversarial transaction schedules at named deterministic checkpoints."""

import asyncio

import pytest

from squid_reactivity import (
    ActionLedger,
    TransactionParticipant,
    LocalTopicBus,
    ReactiveConflictError,
    SharedState,
    add_action_result_sink,
    enlist,
    on_action_commit,
    state,
    strong_read,
    transaction,
)
from squid_reactivity.testing import InterleavingHarness


class Model(SharedState[str]):
    source: str = state("A")
    result: str = state("")


def test_scheduler_reproduces_read_a_write_b_write_skew() -> None:
    model = Model(LocalTopicBus(), "model")
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "source", "B"))

    with (
        schedule.installed(),
        pytest.raises(ReactiveConflictError, match=r"Model\('model'\)\.source changed"),
        transaction(),
        strong_read(),
    ):
        model.result = model.source.lower()

    assert model.source == "B"
    assert model.result == ""
    assert "commit.before_validation" in schedule.seen


def test_scheduler_reproduces_a_b_a_lineage_change() -> None:
    model = Model(LocalTopicBus(), "model")
    schedule = InterleavingHarness()

    def move_twice() -> None:
        model.source = "B"
        model.source = "A"

    schedule.at("transaction.close_staging", move_twice)
    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction(), strong_read():
        assert model.source == "A"
        model.result = "derived"

    assert model.source == "A"
    assert model.result == ""


def test_scheduler_classifies_cancellation_before_publication_once() -> None:
    model = Model(LocalTopicBus(), "model")
    schedule = InterleavingHarness()
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    schedule.at("commit.before_validation", lambda: (_ for _ in ()).throw(asyncio.CancelledError()))
    try:
        with schedule.installed(), pytest.raises(asyncio.CancelledError), transaction():
            model.result = "never"
    finally:
        ledger.close()

    assert model.result == ""
    assert len(ledger.results) == 1
    assert ledger.results[0].terminal == "rolled_back"
    assert ledger.results[0].reason == "cancelled"


def test_scheduler_observes_prepare_abort_and_failure_isolated_hook_order() -> None:
    schedule = InterleavingHarness()
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    calls: list[str] = []

    class Participant:
        def prepare(self, view) -> None:
            calls.append("prepare")
            raise RuntimeError("prepare rejected")

        def describe_change(self, prepared: None) -> None:
            return None

        def apply(self, prepared: None) -> None:
            raise AssertionError("unreachable")

        def abort(self, prepared: None, cause: BaseException) -> None:
            calls.append("abort")

        def finalize(self, prepared: None) -> None:
            raise AssertionError("unreachable")

    try:
        with schedule.installed(), pytest.raises(RuntimeError, match="prepare rejected"), transaction():
            enlist(object(), Participant)
    finally:
        ledger.close()

    assert calls == ["prepare", "abort"]
    assert len(ledger.results) == 1
    assert ledger.results[0].reason == "participant_prepare_failed"
    assert schedule.seen.index("commit.before_participant_prepare") < schedule.seen.index("rollback.before_abort")

    hook_schedule = InterleavingHarness()
    with hook_schedule.installed(), transaction():
        on_action_commit(lambda commit, continuation: (_ for _ in ()).throw(RuntimeError("hook")))
    assert "continuation.before_hook" in hook_schedule.seen


def test_scheduler_records_read_only_noop_and_integrity_commit_exactly_once() -> None:
    ledger = ActionLedger()
    add_action_result_sink(ledger)
    read_only = InterleavingHarness()
    try:
        with read_only.installed(), transaction():
            pass
        assert len(ledger.results) == 1
        assert ledger.results[0].terminal == "committed"
        assert ledger.results[0].changes.cells == 0
        assert read_only.seen[:2] == ["transaction.enter_body", "transaction.close_staging"]

        class BrokenParticipant:
            def prepare(self, view) -> None:
                return None

            def describe_change(self, prepared: None) -> None:
                return None

            def apply(self, prepared: None) -> None:
                raise RuntimeError("integrity failure")

            def abort(self, prepared: None, cause: BaseException) -> None:
                pass

            def finalize(self, prepared: None) -> None:
                pass

        integrity = InterleavingHarness()
        with integrity.installed(), pytest.raises(RuntimeError, match="integrity failure"), transaction():
            enlist(object(), BrokenParticipant)
    finally:
        ledger.close()

    assert len(ledger.results) == 2
    assert ledger.results[1].terminal == "committed"
    assert ledger.results[1].tags == frozenset({"framework_integrity_failure"})
    assert "commit.before_publication" in integrity.seen
    assert "commit.after_cell_publication" in integrity.seen


def test_change_description_failure_aborts_with_the_prepared_value() -> None:
    prepared_value = object()
    aborted: list[object | None] = []
    ledger = ActionLedger()
    add_action_result_sink(ledger)

    class Participant(TransactionParticipant[object]):
        def prepare(self, view) -> object:
            return prepared_value

        def describe_change(self, prepared: object) -> None:
            assert prepared is prepared_value
            raise RuntimeError("cannot describe change")

        def apply(self, prepared: object) -> None:
            raise AssertionError("unreachable")

        def abort(self, prepared: object | None, cause: BaseException) -> None:
            aborted.append(prepared)

        def finalize(self, prepared: object) -> None:
            raise AssertionError("unreachable")

    try:
        with pytest.raises(RuntimeError, match="describe change"), transaction():
            enlist(object(), Participant)
    finally:
        ledger.close()

    assert aborted == [prepared_value]
    assert len(ledger.results) == 1
    assert ledger.results[0].reason == "participant_prepare_failed"

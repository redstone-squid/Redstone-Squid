"""Adversarial transaction schedules at named deterministic checkpoints."""

import asyncio

import pytest

from squid_reactive import (
    ActionLedger,
    LocalTopicBus,
    ReactiveConflictError,
    Shared,
    add_action_outcome_sink,
    join_action,
    on_action_commit,
    state,
    transaction,
)
from squid_reactive.testing import InterleavingHarness


class Model(Shared[str]):
    source: str = state("A")
    result: str = state("")


def test_scheduler_reproduces_read_a_write_b_write_skew() -> None:
    model = Model(LocalTopicBus(), "model")
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "source", "B"))

    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction():
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
    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction():
        assert model.source == "A"
        model.result = "derived"

    assert model.source == "A"
    assert model.result == ""


def test_scheduler_classifies_cancellation_before_publication_once() -> None:
    model = Model(LocalTopicBus(), "model")
    schedule = InterleavingHarness()
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    schedule.at("commit.before_validation", lambda: (_ for _ in ()).throw(asyncio.CancelledError()))
    try:
        with schedule.installed(), pytest.raises(asyncio.CancelledError), transaction():
            model.result = "never"
    finally:
        ledger.close()

    assert model.result == ""
    assert len(ledger.outcomes) == 1
    assert ledger.outcomes[0].terminal == "rolled_back"
    assert ledger.outcomes[0].reason == "cancelled"


def test_scheduler_observes_prepare_abort_and_failure_isolated_hook_order() -> None:
    schedule = InterleavingHarness()
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
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
            join_action(object(), Participant)
    finally:
        ledger.close()

    assert calls == ["prepare", "abort"]
    assert len(ledger.outcomes) == 1
    assert ledger.outcomes[0].reason == "participant_prepare_failed"
    assert schedule.seen.index("commit.before_participant_prepare") < schedule.seen.index("rollback.before_abort")

    hook_schedule = InterleavingHarness()
    with hook_schedule.installed(), transaction():
        on_action_commit(lambda commit, aftermath: (_ for _ in ()).throw(RuntimeError("hook")))
    assert "aftermath.before_hook" in hook_schedule.seen


def test_scheduler_records_read_only_noop_and_integrity_commit_exactly_once() -> None:
    ledger = ActionLedger()
    add_action_outcome_sink(ledger)
    read_only = InterleavingHarness()
    try:
        with read_only.installed(), transaction():
            pass
        assert len(ledger.outcomes) == 1
        assert ledger.outcomes[0].terminal == "committed"
        assert ledger.outcomes[0].changes.cells == 0
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
            join_action(object(), BrokenParticipant)
    finally:
        ledger.close()

    assert len(ledger.outcomes) == 2
    assert ledger.outcomes[1].terminal == "committed"
    assert ledger.outcomes[1].tags == frozenset({"framework_integrity_failure"})
    assert "commit.before_publication" in integrity.seen
    assert "commit.after_cell_publication" in integrity.seen

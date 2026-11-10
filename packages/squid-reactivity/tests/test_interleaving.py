"""Inject competing writes and failures at specific transaction phases.

InterleavingHarness runs callbacks at named checkpoints. This makes races such as
a write just before commit validation reproducible without relying on task timing.
"""

import asyncio
from typing import override

import pytest

from squid_reactivity import (
    EventLedger,
    Reactive,
    ReactiveConflictError,
    TransactionParticipant,
    enlist,
    observe_events,
    on_action_commit,
    state,
    strong_read,
    transaction,
)
from squid_reactivity.testing import InterleavingHarness


class Model(Reactive):
    source: str = state("A")
    result: str = state("")


def test_scheduler_reproduces_read_a_write_b_write_skew() -> None:
    model = Model()
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "source", "B"))

    with (
        schedule.installed(),
        pytest.raises(ReactiveConflictError, match=r"\.source changed"),
        transaction(),
        strong_read(),
    ):
        model.result = model.source.lower()

    assert model.source == "B"
    assert model.result == ""
    assert "commit.before_validation" in schedule.seen


def test_scheduler_reproduces_a_b_a_lineage_change() -> None:
    model = Model()
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
    model = Model()
    schedule = InterleavingHarness()
    ledger = EventLedger()
    with observe_events(ledger):
        schedule.at("commit.before_validation", lambda: (_ for _ in ()).throw(asyncio.CancelledError()))
        with schedule.installed(), pytest.raises(asyncio.CancelledError), transaction():
            model.result = "never"

        assert model.result == ""
        assert len(ledger.results) == 1
        assert ledger.results[0].terminal == "rolled_back"
        assert ledger.results[0].reason == "cancelled"


def test_scheduler_observes_prepare_abort_and_failure_isolated_hook_order() -> None:
    schedule = InterleavingHarness()
    ledger = EventLedger()
    with observe_events(ledger):
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

        with schedule.installed(), pytest.raises(RuntimeError, match="prepare rejected"), transaction():
            enlist(object(), Participant)

        assert calls == ["prepare", "abort"]
        assert len(ledger.results) == 1
        assert ledger.results[0].reason == "participant_prepare_failed"
        assert schedule.seen.index("commit.before_participant_prepare") < schedule.seen.index("rollback.before_abort")

        hook_schedule = InterleavingHarness()
        with hook_schedule.installed(), transaction():
            on_action_commit(lambda commit: (_ for _ in ()).throw(RuntimeError("hook")))
        assert "continuation.before_hook" in hook_schedule.seen


def test_scheduler_records_read_only_noop_and_integrity_commit_exactly_once() -> None:
    ledger = EventLedger()
    with observe_events(ledger):
        read_only = InterleavingHarness()
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

        assert len(ledger.results) == 2
        assert ledger.results[1].terminal == "committed"
        assert ledger.results[1].tags == frozenset({"framework_integrity_failure"})
        assert "commit.before_publication" in integrity.seen
        assert "commit.after_cell_publication" in integrity.seen


def test_change_description_failure_aborts_with_the_prepared_value() -> None:
    prepared_value = object()
    aborted: list[object | None] = []
    ledger = EventLedger()
    with observe_events(ledger):

        class Participant(TransactionParticipant[object]):
            @override
            def prepare(self, view) -> object:
                return prepared_value

            @override
            def describe_change(self, prepared: object) -> None:
                assert prepared is prepared_value
                raise RuntimeError("cannot describe change")

            @override
            def apply(self, prepared: object) -> None:
                raise AssertionError("unreachable")

            @override
            def abort(self, prepared: object | None, cause: BaseException) -> None:
                aborted.append(prepared)

            @override
            def finalize(self, prepared: object) -> None:
                raise AssertionError("unreachable")

        with pytest.raises(RuntimeError, match="describe change"), transaction():
            enlist(object(), Participant)

        assert aborted == [prepared_value]
        assert len(ledger.results) == 1
        assert ledger.results[0].reason == "participant_prepare_failed"

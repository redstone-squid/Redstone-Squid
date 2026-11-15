"""Report effective validation conditions without running sinks during commit."""

import gc
import weakref
from contextvars import Context
from typing import override

import pytest

from squid_reactivity import (
    EventLedger,
    Reactive,
    ReactiveConflictError,
    TransactionCommit,
    observe_events,
    relaxed_read,
    state,
    strong_read,
    transaction,
)
from squid_reactivity.diagnostics import CausalEventSnapshot, TransactionValidationSnapshot
from squid_reactivity.transactions import ConditionalCellPatch, SlotValue, apply_conditional_patches


class Model(Reactive):
    value: int = state(0)
    other: int = state(0)


def validations(ledger: EventLedger) -> list[TransactionValidationSnapshot]:
    return [event for event in ledger.events if isinstance(event, TransactionValidationSnapshot)]


def test_validation_is_opt_in_per_sink_and_contains_no_live_owners() -> None:
    normal, detailed = EventLedger(), EventLedger()
    model = Model()
    reference = weakref.ref(model)
    with observe_events(normal), observe_events(detailed, include_validation=True), transaction() as tx:
        model.value += 1
    assert not validations(normal)
    (snapshot,) = validations(detailed)
    (condition,) = snapshot.preconditions
    assert snapshot.transaction_id == str(tx.context.transaction_id)
    assert condition.origin == "read_write"
    assert condition.expected_version == condition.actual_version == 0
    assert isinstance(tx.result, TransactionCommit)
    assert snapshot.timestamp <= tx.result.committed_at
    del tx, model
    gc.collect()
    assert reference() is None


def test_origins_follow_effective_precedence_and_first_strong_read_version() -> None:
    model = Model()
    with transaction() as seed:
        model.value = 1
    assert isinstance(seed.result, TransactionCommit)
    target = next(iter(seed.result.patches)).target
    ledger = EventLedger()
    with observe_events(ledger, include_validation=True), transaction():
        _ = model.value  # Ordinary read sees version 1.
        Context().run(setattr, model, "value", 2)
        with strong_read():
            _ = model.value  # Strong read replaces the required version with 2.
        model.value = 3
    (condition,) = validations(ledger)[0].preconditions
    assert condition.origin == "strong_read"
    assert condition.expected_version == condition.actual_version == 2

    with observe_events(ledger, include_validation=True), transaction():
        with strong_read():
            _ = model.value
        apply_conditional_patches((ConditionalCellPatch(target, SlotValue(present=True, value=4), 3),))
    (condition,) = validations(ledger)[1].preconditions
    assert condition.origin == "explicit"
    assert condition.expected_version == condition.actual_version == 3


def test_unchecked_reads_are_excluded_and_empty_transactions_have_no_conditions() -> None:
    model = Model()
    ledger = EventLedger()
    with observe_events(ledger, include_validation=True):
        with transaction(), strong_read():
            with relaxed_read():
                _ = model.value
            model.value = 1
        with transaction():
            _ = model.value  # Not written: this ordinary read imposes no condition.
            model.other = 1  # Blind write.
        with transaction(), strong_read():
            _ = model.value  # No writes or participants: validation does not apply.
    assert [event.preconditions for event in validations(ledger)] == [(), (), ()]


def test_conflict_reports_versions_at_validation_and_does_not_publish() -> None:
    model = Model()
    ledger = EventLedger()
    with observe_events(ledger, include_validation=True), pytest.raises(ReactiveConflictError), transaction():
        model.value += 1
        Context().run(setattr, model, "value", 10)
    (condition,) = validations(ledger)[0].preconditions
    assert (condition.expected_version, condition.actual_version) == (0, 1)
    assert model.value == 10
    assert ledger.results[0].reason == "conflict"


def test_body_failure_has_no_validation_event() -> None:
    ledger = EventLedger()
    with observe_events(ledger, include_validation=True), pytest.raises(ValueError), transaction():
        raise ValueError("before validation")
    assert not validations(ledger)


def test_innermost_observer_options_win_and_failures_cannot_undo_commit() -> None:
    class Sink(EventLedger):
        @override
        def accept(self, result: CausalEventSnapshot) -> None:
            super().accept(result)
            if isinstance(result, TransactionValidationSnapshot):
                assert model.value == 2  # Never called before publication.
                raise RuntimeError("diagnostic failure")  # noqa: TRY004

    ledger = Sink()
    model = Model()
    with observe_events(ledger, include_validation=True):
        with observe_events(ledger), transaction():
            model.value += 1
        assert not validations(ledger)
        with transaction() as tx:
            model.value += 1
    assert isinstance(tx.result, TransactionCommit)
    assert len(validations(ledger)) == 1
    assert len(ledger.results) == 2

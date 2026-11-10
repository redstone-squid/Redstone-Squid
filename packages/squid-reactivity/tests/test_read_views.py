"""Computed caches preserve transaction isolation and read consistency policies."""

from contextvars import Context

import pytest

from squid_reactivity import (
    Reactive,
    ReactiveConflictError,
    computed,
    observe_reads,
    relaxed_read,
    state,
    strong_read,
    transaction,
    untracked,
)
from squid_reactivity.testing import InterleavingHarness


class Model(Reactive):
    value: int = state(0)
    result: int = state(0)

    @computed
    def doubled(self) -> int:
        return self.value * 2

    @computed
    def nested(self) -> int:
        return self.doubled + 1


def test_independent_reader_cannot_see_an_overlay_through_a_computed() -> None:
    model = Model()
    with transaction():
        model.value = 10
        assert model.nested == 21
        assert Context().run(lambda: (model.value, model.nested)) == (0, 1)
        assert model.nested == 21
    assert model.nested == 21


def test_two_overlays_with_the_same_revision_have_distinct_computed_values() -> None:
    model = Model()

    def competing_action() -> None:
        with transaction():
            model.value = 20
            assert model.nested == 41

    with transaction():
        model.value = 10
        assert model.nested == 21
        Context().run(competing_action)
        assert model.nested == 21
    assert model.value == 10
    assert model.nested == 21


@pytest.mark.parametrize("warm_inside", [False, True])
@pytest.mark.parametrize("relaxed", [False, True])
def test_cached_computed_reads_become_strong_in_the_callers_scope(warm_inside: bool, relaxed: bool) -> None:
    model = Model()
    assert model.nested == 1
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "value", 5))
    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction():
        if warm_inside:
            with relaxed_read() if relaxed else untracked():
                assert model.nested == 1
        with strong_read():
            model.result = model.nested
    assert model.result == 0


def test_cached_read_then_write_still_detects_a_competing_write() -> None:
    model = Model()
    assert model.doubled == 0
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "value", 5))
    with schedule.installed(), pytest.raises(ReactiveConflictError), transaction():
        model.value = model.doubled + 1
    assert model.value == 5


def test_relaxed_computed_read_remains_exempt_from_conflict_validation() -> None:
    model = Model()
    assert model.nested == 1
    schedule = InterleavingHarness()
    schedule.at("commit.before_validation", lambda: setattr(model, "value", 5))
    with schedule.installed(), transaction(), strong_read(), relaxed_read():
        model.result = model.nested
    assert model.result == 1


def test_view_switch_preserves_equal_result_backdating() -> None:
    model = Model()
    with observe_reads() as reads:
        assert model.nested == 1
    with transaction():
        assert model.nested == 1
    assert reads.current()


def test_repeated_reads_in_one_view_reuse_the_cache_and_rollback_discards_it() -> None:
    calls: list[int] = []

    class Counted(Model):
        @computed
        def counted(self) -> int:
            calls.append(self.value)
            return self.doubled

    model = Counted()
    with pytest.raises(ValueError), transaction():
        model.value = 10
        assert model.counted == model.counted == 20
        assert calls == [10]
        raise ValueError("abort")
    assert model.counted == 0
    assert calls == [10, 0]

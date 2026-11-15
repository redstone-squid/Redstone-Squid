"""Computed caches preserve transaction isolation and read consistency policies."""

import weakref
from contextlib import nullcontext
from contextvars import Context, copy_context

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
from squid_reactivity.inspection import inspect_computed
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


def test_empty_transactions_preserve_the_committed_cache_but_evaluate_their_own() -> None:
    calls: list[int] = []

    class Counted(Model):
        @computed(reuse=False)
        def counted(self) -> int:
            calls.append(self.value)
            return self.value

    model = Counted()
    assert model.counted == 0
    for expected_calls in (2, 3):
        with transaction():
            assert model.counted == model.counted == 0
            assert len(calls) == expected_calls
        assert model.counted == 0
        assert len(calls) == expected_calls


@pytest.mark.parametrize("rollback", [False, True])
def test_staged_cache_does_not_evict_committed_cache(rollback: bool) -> None:
    calls: list[int] = []

    class Counted(Model):
        @computed
        def counted(self) -> int:
            calls.append(self.value)
            return self.value

    model = Counted()
    assert model.counted == 0
    with pytest.raises(ValueError, match="abort") if rollback else nullcontext(), transaction():
        model.value = 10
        assert model.counted == 10
        assert Context().run(lambda: model.counted) == 0
        assert model.counted == 10
        assert calls == [0, 10]
        if rollback:
            raise ValueError("abort")
    assert model.counted == (0 if rollback else 10)
    assert calls == ([0, 10] if rollback else [0, 10, 10])


def test_interleaved_transactions_keep_their_own_cache() -> None:
    calls: list[int] = []

    class Counted(Model):
        @computed
        def counted(self) -> int:
            calls.append(self.value)
            return self.value

    model = Counted()

    def competing() -> None:
        with transaction():
            model.value = 20
            assert model.counted == 20

    with transaction():
        model.value = 10
        assert model.counted == 10
        Context().run(competing)
        assert model.counted == 10
    assert calls == [10, 20]
    assert model.counted == 10
    assert calls == [10, 20, 10]


def test_switching_read_policies_reuses_each_cache_and_preserves_strong_reads() -> None:
    calls: list[int] = []

    class Counted(Model):
        @computed(reuse=False)
        def counted(self) -> int:
            calls.append(self.value)
            return self.value

    model = Counted()
    with pytest.raises(ReactiveConflictError), transaction():
        for _ in range(2):
            assert model.counted == 0
            with relaxed_read():
                assert model.counted == 0
            with strong_read():
                assert model.counted == 0
        assert calls == [0, 0, 0]
        model.result = 1
        Context().run(setattr, model, "value", 1)
    assert model.result == 0


def test_equal_committed_results_do_not_recompute_downstream_after_staged_reads() -> None:
    calls: list[str] = []

    class Counted(Model):
        @computed
        def parity(self) -> int:
            calls.append("parity")
            return self.value % 2

        @computed
        def label(self) -> str:
            calls.append("label")
            return str(self.parity)

    model = Counted()
    assert model.label == "0"
    with transaction():
        model.value = 2
        assert model.label == "0"
    calls.clear()
    assert model.label == "0"
    assert calls == ["parity"]


def test_dependency_graph_and_inspection_follow_the_current_view() -> None:
    class Branch(Model):
        @computed
        def selected(self) -> int:
            return self.value if self.result == 0 else 42

    model = Branch()
    with observe_reads() as committed:
        assert model.selected == 0
    before = inspect_computed(model)["selected"]
    assert len(committed.addresses()) == 2
    with transaction():
        assert not inspect_computed(model)["selected"].evaluated
        model.result = 1
        with observe_reads() as staged:
            assert model.selected == 42
        assert len(staged.addresses()) == 1
        assert inspect_computed(model)["selected"].version != before.version
        assert not committed.current()
        assert len(Context().run(committed.addresses)) == 2
        assert len(staged.addresses()) == 1
        assert Context().run(inspect_computed, model)["selected"] == before
    assert not committed.current()
    assert model.selected == 42


@pytest.mark.parametrize("rollback", [False, True])
def test_closed_transactions_release_cached_values_even_in_copied_contexts(rollback: bool) -> None:
    class Value:
        pass

    class Owner(Reactive):
        @computed
        def box(self) -> Value:
            return Value()

    owner = Owner()
    with pytest.raises(ValueError, match="abort") if rollback else nullcontext(), transaction():
        reference = weakref.ref(owner.box)
        inherited = copy_context()
        assert reference() is not None
        if rollback:
            raise ValueError("abort")
    assert reference() is None
    assert inherited.run(lambda: owner.box) is owner.box

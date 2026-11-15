"""Cross-transaction cache hits preserve the reads an evaluation would perform."""

import gc
import weakref
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import Context
from dataclasses import dataclass

import pytest

from squid_reactivity import (
    LocalTopicBus,
    Reactive,
    ReactiveConflictError,
    _core,
    _read_capture,
    computed,
    current_transaction,
    observe_reads,
    relaxed_read,
    state,
    strong_read,
    transaction,
    untracked,
)
from squid_reactivity._async_values import observe_async_bindings
from squid_reactivity.inspection import inspect_cells, inspect_computed
from squid_reactivity.operations import Pending, ProgressReporter, operation
from squid_reactivity.resources import resource
from squid_reactivity.transactions import current_causality


@pytest.fixture(autouse=True)
def admit_computed_results(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercise reuse deterministically without making test outcomes depend on timing.
    monkeypatch.setattr(_core, "_MIN_REUSE_NS", 0)


@contextmanager
def consistency(*policies: str) -> Iterator[None]:
    with ExitStack() as scopes:
        for policy in policies:
            if policy == "strong":
                scopes.enter_context(strong_read())
            elif policy == "relaxed":
                scopes.enter_context(relaxed_read())
        yield


class Model(Reactive):
    value: int = state(0)
    result: int = state(0)


@pytest.mark.parametrize("original", ["default", "strong", "relaxed"])
@pytest.mark.parametrize("caller", ["default", "strong", "relaxed"])
@pytest.mark.parametrize("inner", [(), ("strong",), ("relaxed",), ("strong", "relaxed"), ("relaxed", "strong")])
def test_reuse_replays_relative_policy(original: str, caller: str, inner: tuple[str, ...]) -> None:
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            with consistency(*inner):
                return self.value + 10

    owner = Owner()
    with consistency(original):
        assert owner.projection == 10
    strong = "relaxed" not in (caller, *inner) and "strong" in (caller, *inner)
    with pytest.raises(ReactiveConflictError) if strong else nullcontext(), transaction():
        with consistency(caller):
            owner.result = owner.projection
        assert calls == [None]
        Context().run(setattr, owner, "value", 1)
    assert owner.result == (0 if strong else 10)


def test_cached_child_footprint_is_replayed_through_parent() -> None:
    calls: list[str] = []

    class Owner(Model):
        @computed(reuse=True)
        def child(self) -> int:
            calls.append("child")
            return self.value

        @computed(reuse=True)
        def parent(self) -> int:
            calls.append("parent")
            return self.child + 10

    owner = Owner()
    assert owner.child == 0
    assert owner.parent == 10
    with pytest.raises(ReactiveConflictError), transaction(), strong_read():
        owner.result = owner.parent
        assert calls == ["child", "parent"]
        Context().run(setattr, owner, "value", 1)
    assert owner.result == 0


def test_parent_relaxed_scope_composes_with_cached_child_strong_scope() -> None:
    calls: list[str] = []

    class Owner(Model):
        @computed(reuse=True)
        def child(self) -> int:
            calls.append("child")
            with strong_read():
                return self.value

        @computed(reuse=True)
        def parent(self) -> int:
            calls.append("parent")
            with relaxed_read():
                return self.child + 10

    owner = Owner()
    assert owner.child == 0
    assert owner.parent == 10
    with transaction(), strong_read():
        owner.result = owner.parent
        assert calls == ["child", "parent"]
        Context().run(setattr, owner, "value", 1)
    assert owner.result == 10


def test_untracked_cached_child_still_contributes_conflict_reads() -> None:
    calls: list[str] = []

    class Owner(Model):
        @computed(reuse=True)
        def child(self) -> int:
            calls.append("child")
            return self.value

        @computed(reuse=True)
        def parent(self) -> int:
            calls.append("parent")
            with untracked():
                return self.child + 10

    owner = Owner()
    assert owner.child == 0
    with observe_reads() as reads:
        assert owner.parent == 10
    assert reads.addresses() == ()
    with pytest.raises(ReactiveConflictError), transaction(), strong_read():
        owner.result = owner.parent
        assert calls == ["child", "parent"]
        Context().run(setattr, owner, "value", 1)


@pytest.mark.parametrize("staged", [False, True])
def test_changed_untracked_input_rejects_reuse(staged: bool) -> None:
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            with untracked():
                return self.value

    owner = Owner()
    assert owner.projection == 0
    if not staged:
        owner.value = 1
    with transaction():
        if staged:
            owner.value = 1
        assert owner.projection == 1
        assert calls == [None, None]


@pytest.mark.parametrize("related", [False, True])
def test_only_staged_inputs_prevent_reuse(related: bool) -> None:
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            return self.value

    owner = Owner()
    assert owner.projection == 0
    with transaction():
        if related:
            owner.value = 1
        else:
            owner.result = 1
        assert owner.projection == (1 if related else 0)
        assert len(calls) == (2 if related else 1)


@pytest.mark.parametrize("earlier_strong", [False, True])
def test_replay_preserves_independent_first_read_versions(earlier_strong: bool) -> None:
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            return self.value

    owner = Owner()
    with pytest.raises(ReactiveConflictError) if earlier_strong else nullcontext(), transaction():
        with consistency("strong" if earlier_strong else "default"):
            assert owner.value == 0
        Context().run(setattr, owner, "value", 1)
        assert Context().run(lambda: owner.projection) == 1
        with strong_read():
            owner.result = owner.projection
        assert calls == [None]
    assert owner.result == (0 if earlier_strong else 1)


def test_equal_child_result_does_not_replay_an_obsolete_input_version() -> None:
    class Owner(Model):
        @computed(reuse=True)
        def parity(self) -> int:
            return self.value % 2

        @computed(reuse=True)
        def projection(self) -> int:
            return self.parity + 10

    owner = Owner()
    assert owner.projection == 10
    owner.value = 2
    assert owner.parity == 0
    # Backdating keeps projection's value valid, but its original cell reads are old.
    assert owner.projection == 10
    with transaction(), strong_read():
        owner.result = owner.projection
    assert owner.result == 10


@pytest.mark.parametrize("hidden", [False, True])
def test_resource_replacement_cannot_reuse_committed_projection(hidden: bool) -> None:
    class Owner(Model):
        @resource
        async def data(self) -> int:
            return 0

        @computed(reuse=True)
        def projection(self) -> int:
            with untracked() if hidden else nullcontext():
                value = self.data.value
                assert value is not None
                return value

    owner = Owner()
    owner.data.replace(10)
    assert owner.projection == 10
    with transaction():
        owner.data.replace(20)
        assert owner.projection == 20


@pytest.mark.parametrize("causal", [False, True])
def test_public_context_reads_prevent_cross_transaction_reuse(causal: bool) -> None:
    class Owner(Reactive):
        @computed(reuse=True)
        def identity(self) -> str | None:
            if causal:
                origin = current_causality()
                return None if origin is None else origin.cause.identity
            current = current_transaction()
            return None if current is None else str(current.context.transaction_id)

    owner = Owner()
    assert owner.identity is None
    with transaction() as first:
        assert owner.identity == str(first.context.transaction_id)
    with transaction() as second:
        assert owner.identity == str(second.context.transaction_id)
    assert owner.identity is None


def test_explicit_opt_out_resamples_ambient_inputs_in_each_transaction() -> None:
    ambient = [0]

    class Owner(Reactive):
        @computed(reuse=False)
        def projection(self) -> int:
            return ambient[0]

    owner = Owner()
    assert owner.projection == 0
    ambient[0] = 1
    with transaction():
        assert owner.projection == 1
    ambient[0] = 2
    with transaction():
        assert owner.projection == 2


def test_default_decorators_keep_ambient_results_local_to_each_view() -> None:
    ambient = [0]
    calls: list[str] = []

    class Owner(Reactive):
        @computed
        def bare(self) -> int:
            calls.append("bare")
            return ambient[0]

        @computed()
        def configured(self) -> int:
            calls.append("configured")
            return ambient[0]

    owner = Owner()
    assert owner.bare == owner.configured == 0
    for value in (1, 2):
        ambient[0] = value
        with transaction():
            assert owner.bare == owner.configured == value
            assert owner.bare == owner.configured == value
        assert owner.bare == owner.configured == 0
    assert calls == ["bare", "configured"] * 3


def test_topic_watch_does_not_become_a_strong_transaction_read() -> None:
    topic = LocalTopicBus().topic("record", "1")
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            topic.watch()
            return self.value + 10

    owner = Owner()
    assert owner.projection == 10
    with transaction(), strong_read():
        owner.result = owner.projection
        assert calls == [None]
        topic.publish()
    assert owner.result == 10


def test_reusable_footprints_do_not_keep_unused_models_alive() -> None:
    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            return self.value

    owner = Owner()
    reference = weakref.ref(owner)
    assert owner.projection == 0
    with transaction():
        assert owner.projection == 0
    del owner
    gc.collect()
    assert reference() is None


def test_replayed_default_read_guards_a_later_write_to_the_same_cell() -> None:
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            return self.value + 1

    owner = Owner()
    assert owner.projection == 1
    with pytest.raises(ReactiveConflictError), transaction():
        owner.value = owner.projection
        assert calls == [None]
        Context().run(setattr, owner, "value", 5)
    assert owner.value == 5


def test_transaction_recomputation_cannot_mutate_the_reusable_committed_cache() -> None:
    calls: list[int] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(self.value)
            return self.value

    owner = Owner()
    assert owner.projection == 0
    with pytest.raises(ValueError, match="abort"), transaction():
        assert owner.projection == 0
        owner.value = 1
        assert owner.projection == 1
        raise ValueError("abort")
    with transaction():
        assert owner.projection == 0
    assert calls == [0, 1]


@pytest.mark.parametrize("explicit_opt_out", [False, True])
def test_parent_cannot_reuse_a_child_without_opt_in(explicit_opt_out: bool) -> None:
    ambient = [0]
    child_computed = computed(reuse=False) if explicit_opt_out else computed()

    class Owner(Reactive):
        @child_computed
        def child(self) -> int:
            return ambient[0]

        @computed(reuse=True)
        def parent(self) -> int:
            return self.child + 10

    owner = Owner()
    assert owner.parent == 10
    ambient[0] = 1
    with transaction():
        assert owner.parent == 11


def test_cheap_computations_can_skip_reusable_footprints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_core, "_MIN_REUSE_NS", 10**30)
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            return self.value

    owner = Owner()
    assert owner.projection == 0
    for _ in range(2):
        with transaction():
            assert owner.projection == owner.projection == 0
    assert calls == [None, None, None]


def test_equal_recomputation_reuses_the_retained_result_object() -> None:
    @dataclass(frozen=True)
    class Value:
        parity: int

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> Value:
            return Value(self.value % 2)

    owner = Owner()
    original = owner.projection
    owner.value = 2
    assert owner.projection is original
    with transaction():
        assert owner.projection is original


def test_oversized_footprints_fall_back_to_evaluation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_read_capture, "_MAX_FOOTPRINT_ENTRIES", 1)
    calls: list[None] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(None)
            return self.value

    owner = Owner()
    assert owner.projection == 0
    for _ in range(2):
        with transaction():
            assert owner.projection == 0
    assert calls == [None, None, None]


def test_repeated_ineligible_views_evict_a_reusable_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_core, "_MAX_REUSE_MISSES", 2)
    calls: list[int] = []

    class Owner(Model):
        @computed(reuse=True)
        def projection(self) -> int:
            calls.append(self.value)
            return self.value

    owner = Owner()
    assert owner.projection == 0
    for _ in range(2):
        with pytest.raises(ValueError, match="abort"), transaction():
            owner.value = 1
            assert owner.projection == 1
            raise ValueError("abort")
    with transaction():
        assert owner.projection == 0
    assert calls == [0, 1, 1, 0]
    # Eviction affects reuse, not the ordinary committed cache.
    assert owner.projection == 0
    assert calls == [0, 1, 1, 0]


def test_caught_child_failure_prevents_reuse_of_partial_reads() -> None:
    calls: list[str] = []

    class Owner(Model):
        @computed(reuse=True)
        def child(self) -> int:
            calls.append("child")
            if self.value == 0:
                raise ValueError("not ready")
            return self.value

        @computed(reuse=True)
        def parent(self) -> int:
            calls.append("parent")
            try:
                return self.child + 10
            except ValueError:
                return 10

    owner = Owner()
    assert owner.parent == 10
    owner.value = 1
    with transaction():
        assert owner.parent == 11
    assert calls == ["parent", "child", "parent", "child"]


def test_operation_status_remains_discoverable_through_a_computed_in_a_new_transaction() -> None:
    class Service:
        @operation(initial=None)
        async def work(self, progress: ProgressReporter[None]) -> int:
            return 42

    execution = Service().work.start()
    calls: list[None] = []

    class Owner(Reactive):
        @computed(reuse=True)
        def waiting(self) -> bool:
            calls.append(None)
            return isinstance(execution.status, Pending)

    owner = Owner()
    assert owner.waiting
    with transaction(), observe_async_bindings() as bindings:
        assert owner.waiting
    assert bindings == [execution]
    assert calls == [None, None]


def test_cell_inspection_resamples_the_current_transaction_view() -> None:
    target = Model()
    calls: list[None] = []

    class Owner(Reactive):
        @computed(reuse=True)
        def revision(self) -> int:
            calls.append(None)
            return inspect_cells(target)["value"].version

    owner = Owner()
    original = owner.revision
    with transaction():
        target.value = 1
        assert owner.revision > original
    assert calls == [None, None]


def test_computed_inspection_resamples_the_current_transaction_view() -> None:
    class Target(Model):
        @computed(reuse=True)
        def doubled(self) -> int:
            return self.value * 2

    target = Target()
    assert target.doubled == 0
    calls: list[None] = []

    class Owner(Reactive):
        @computed(reuse=True)
        def evaluated(self) -> bool:
            calls.append(None)
            return inspect_computed(target)["doubled"].evaluated

    owner = Owner()
    assert owner.evaluated
    with transaction():
        assert not owner.evaluated
    assert calls == [None, None]

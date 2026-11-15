"""Independent threads own separate reactive graphs and execution contexts."""

import asyncio
from collections.abc import Callable
from concurrent.futures import Future
from contextvars import Context, copy_context
from threading import Barrier, Thread

import pytest

from squid_reactivity import (
    EventLedger,
    LocalTopicBus,
    Reactive,
    StaleReactiveContextError,
    Subscriptions,
    TransactionCommit,
    computed,
    current_transaction,
    observe_events,
    observe_reads,
    state,
    transaction,
)
from squid_reactivity._runtime import _runtime
from squid_reactivity.resources import Ready, resource
from squid_reactivity.testing import InterleavingHarness
from squid_reactivity.transactions import CommitSequence


class Model(Reactive):
    value: int = state(0)

    @computed
    def doubled(self) -> int:
        return self.value * 2

    @resource
    async def loaded(self) -> int:
        value = self.value
        await asyncio.sleep(0)
        return value


def _start[ValueT](work: Callable[[], ValueT], *, context: Context) -> tuple[Thread, Future[ValueT]]:
    result: Future[ValueT] = Future()

    def run() -> None:
        try:
            result.set_result(work())
        except BaseException as error:
            result.set_exception(error)

    thread = Thread(target=run, context=context, daemon=True)
    thread.start()
    return thread, result


def test_independent_event_loops_keep_state_and_outcomes_isolated() -> None:
    barrier = Barrier(4, timeout=10)

    async def exercise() -> list[CommitSequence]:
        model = Model()
        ledger = EventLedger(limit=1000)
        bus = LocalTopicBus()
        topic = bus.topic("thread", "same-name")
        seen: list[object] = []
        subscriptions = Subscriptions(seen.append)
        with observe_reads() as dependencies:
            assert model.doubled == 0
            topic.watch()
        subscriptions.stage(dependencies.addresses())
        subscriptions.commit()
        sequences: list[CommitSequence] = []
        schedule = InterleavingHarness()
        barrier.wait()
        with observe_events(ledger), schedule.installed():
            for value in range(1, 101):
                with transaction() as tx:
                    model.value = value
                    await asyncio.sleep(0)
                    assert model.doubled == value * 2
                assert isinstance(tx.result, TransactionCommit)
                sequences.append(tx.result.sequence)
                assert model.doubled == value * 2
                assert await model.loaded.resolve() == Ready(value)
                bus.publish(topic)
        subscriptions.close()
        assert len(seen) == 200
        assert len(ledger.results) == 100
        assert schedule.seen.count("commit.before_validation") == 100
        return sequences

    workers = [_start(lambda: asyncio.run(exercise()), context=Context()) for _ in range(4)]
    results = [future.result(timeout=30) for _, future in workers]
    for thread, _ in workers:
        thread.join(timeout=5)
        assert not thread.is_alive()
    assert len({sequence.runtime_id for sequences in results for sequence in sequences}) == 4
    for sequences in results:
        assert [sequence.value for sequence in sequences] == list(range(1, 101))


def test_worker_writes_do_not_advance_parent_cache_epoch() -> None:
    model = Model()
    assert model.doubled == 0
    epoch = _runtime.epoch

    def work() -> None:
        other = Model()
        other.value = 42
        assert other.doubled == 84

    thread, result = _start(work, context=Context())
    result.result(timeout=10)
    thread.join(timeout=5)
    assert _runtime.epoch == epoch
    model.value = 1
    assert model.doubled == 2


@pytest.mark.parametrize("access", ["read", "write", "construct", "nested", "handle"])
def test_copied_transaction_context_is_rejected_on_another_thread(access: str) -> None:
    model = Model()
    with transaction() as tx:
        model.value = 7

        def work() -> None:
            if access == "read":
                _ = model.value
            elif access == "write":
                model.value = 99
            elif access == "construct":
                Model()
            elif access == "nested":
                with transaction():
                    pass
            else:
                tx.after_commit(lambda _: None)

        thread, result = _start(work, context=copy_context())
        with pytest.raises(StaleReactiveContextError, match="another thread"):
            result.result(timeout=10)
        thread.join(timeout=5)
        assert current_transaction() is tx
        assert model.value == 7
    assert model.value == 7


def test_clean_worker_context_does_not_inherit_parent_transaction_or_observers() -> None:
    ledger = EventLedger()
    with transaction() as parent, observe_events(ledger), observe_reads() as dependencies:

        def work() -> None:
            assert current_transaction() is None
            with transaction() as child:
                model = Model()
                model.value = 10
                assert model.doubled == 20
            assert child.context != parent.context

        thread, result = _start(work, context=Context())
        result.result(timeout=10)
        thread.join(timeout=5)
        assert not dependencies.sources
        assert not ledger.events

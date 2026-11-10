"""Reusable reactive objects notify consumers without host or bus injection."""

import gc
import weakref

import pytest

from squid_reactivity import Reactive, SubscriptionReconciler, addresses, computed, state, transaction


class Search(Reactive):
    query: str = state("")

    @computed
    def normalized(self) -> str:
        return self.query.strip().lower()


def test_direct_observation_follows_computed_inputs_and_rollback() -> None:
    search = Search()
    seen = []
    subscription = SubscriptionReconciler(None, seen.append)
    subscription.stage(addresses(lambda: search.normalized))
    subscription.commit()
    with transaction():
        search.query = "first"
        search.query = " SECOND "
    assert seen == list(addresses(lambda: search.query))
    assert search.normalized == "second"
    with pytest.raises(ValueError), transaction():
        search.query = "discarded"
        raise ValueError("abort")
    assert len(seen) == 1
    assert search.normalized == "second"
    subscription.close()
    search.query = "closed"
    assert len(seen) == 1


def test_long_lived_source_does_not_retain_its_consumer() -> None:
    search = Search()

    class Consumer:
        def changed(self, address: object) -> None:
            pass

    consumer = Consumer()
    reference = weakref.ref(consumer)
    subscription = SubscriptionReconciler(None, consumer.changed)
    subscription.stage(addresses(lambda: search.query))
    subscription.commit()
    del consumer, subscription
    gc.collect()
    assert reference() is None
    search.query = "still usable"

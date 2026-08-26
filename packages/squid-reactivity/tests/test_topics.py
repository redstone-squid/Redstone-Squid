"""Contracts for tracked topics, synchronous publication, and reconciliation."""

import logging
from collections.abc import Callable

import pytest

from squid_reactivity import LocalTopicBus, SubscriptionReconciler, Topic, TopicBus, observe_reads, watch
from squid_reactivity.topics import Address

BUILD = Topic("build", "1")
OTHER = Topic("build", "2")


def test_local_bus_satisfies_the_protocol_and_delivers_in_order() -> None:
    bus = LocalTopicBus()
    seen: list[str] = []

    bus.subscribe(BUILD, lambda _: seen.append("first"))
    bus.subscribe(BUILD, lambda _: seen.append("second"))
    bus.publish(BUILD)

    assert isinstance(bus, TopicBus)
    assert seen == ["first", "second"]


def test_subscriber_failures_are_isolated_and_reported() -> None:
    reported: list[tuple[Address, Exception]] = []
    seen: list[Address] = []
    bus = LocalTopicBus(on_subscriber_error=lambda address, _callback, error: reported.append((address, error)))

    def fail(_address: Address) -> None:
        raise RuntimeError("broken")

    bus.subscribe(BUILD, fail)
    bus.subscribe(BUILD, seen.append)
    bus.publish(BUILD)

    assert seen == [BUILD]
    assert reported[0][0] == BUILD
    assert str(reported[0][1]) == "broken"


def test_error_hook_failures_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    def fail_hook(_address: Address, _callback: Callable[[Address], None], _error: Exception) -> None:
        raise RuntimeError("reporter failed")

    def fail_subscriber(_address: Address) -> None:
        raise RuntimeError("subscriber failed")

    bus = LocalTopicBus(on_subscriber_error=fail_hook)
    bus.subscribe(BUILD, fail_subscriber)

    with caplog.at_level(logging.ERROR):
        bus.publish(BUILD)

    assert "error hook failed" in caplog.text


def test_publish_moves_a_watched_version_without_subscribers() -> None:
    with observe_reads() as observation:
        watch(BUILD)
    source, version = next(iter(observation.sources.items()))

    LocalTopicBus().publish(BUILD)

    assert source.settle() != version


def test_reconciler_keeps_committed_and_one_staged_set() -> None:
    bus = LocalTopicBus()
    seen: list[Address] = []
    subscriptions = SubscriptionReconciler(bus, seen.append)

    subscriptions.stage((BUILD, BUILD))
    assert subscriptions.followed == (BUILD,)
    subscriptions.commit()

    subscriptions.stage((OTHER,))
    assert subscriptions.followed == (BUILD, OTHER)
    with pytest.raises(RuntimeError, match="already staged"):
        subscriptions.stage((BUILD,))
    subscriptions.discard()

    assert subscriptions.committed == (BUILD,)
    assert subscriptions.followed == (BUILD,)
    bus.publish(BUILD, OTHER)
    assert seen == [BUILD]


def test_reconciler_commit_promotes_and_close_releases() -> None:
    bus = LocalTopicBus()
    subscriptions = SubscriptionReconciler(bus, lambda _address: None)
    subscriptions.stage((BUILD,))
    subscriptions.commit()
    subscriptions.stage((OTHER,))
    subscriptions.commit()

    assert subscriptions.committed == (OTHER,)
    assert subscriptions.followed == (OTHER,)
    subscriptions.close()
    subscriptions.close()
    assert bus.snapshot().topics == ()


def test_reconciler_unwinds_a_partial_stage() -> None:
    class BrokenBus(LocalTopicBus):
        def subscribe[AddressT: Address](
            self, address: AddressT, callback: Callable[[AddressT], None]
        ) -> Callable[[], None]:
            if address == OTHER:
                raise RuntimeError("cannot subscribe")
            return super().subscribe(address, callback)

    bus = BrokenBus()
    subscriptions = SubscriptionReconciler(bus, lambda _address: None)

    with pytest.raises(RuntimeError, match="cannot subscribe"):
        subscriptions.stage((BUILD, OTHER))

    assert subscriptions.staged is None
    assert subscriptions.followed == ()
    assert bus.snapshot().topics == ()

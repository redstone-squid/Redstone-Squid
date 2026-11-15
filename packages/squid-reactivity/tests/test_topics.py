"""Contracts for tracked topics, synchronous publication, and subscriptions."""

import logging
from collections.abc import Callable
from typing import Any, cast, override

import pytest

from squid_reactivity import LocalTopicBus, Subscriptions, TopicBus, TopicName, observe_reads, watch
from squid_reactivity.topics import BusAddress

BUILD = TopicName("build", "1")
OTHER = TopicName("build", "2")


def test_local_bus_satisfies_the_protocol_and_delivers_in_order() -> None:
    bus = LocalTopicBus()
    seen: list[str] = []

    bus.subscribe(BUILD, lambda _: seen.append("first"))
    bus.subscribe(BUILD, lambda _: seen.append("second"))
    bus.publish(BUILD)

    assert isinstance(bus, TopicBus)
    assert seen == ["first", "second"]


def test_subscriber_failures_are_isolated_and_reported() -> None:
    reported: list[tuple[BusAddress, Exception]] = []
    seen: list[BusAddress] = []
    bus = LocalTopicBus(on_subscriber_error=lambda address, _callback, error: reported.append((address, error)))

    def fail(_address: BusAddress) -> None:
        raise RuntimeError("broken")

    bus.subscribe(BUILD, fail)
    bus.subscribe(BUILD, seen.append)
    bus.publish(BUILD)

    assert seen == [BUILD]
    assert reported[0][0] == BUILD
    assert str(reported[0][1]) == "broken"


def test_error_hook_failures_are_isolated(caplog: pytest.LogCaptureFixture) -> None:
    def fail_hook(_address: BusAddress, _callback: Callable[[BusAddress], None], _error: Exception) -> None:
        raise RuntimeError("reporter failed")

    def fail_subscriber(_address: BusAddress) -> None:
        raise RuntimeError("subscriber failed")

    bus = LocalTopicBus(on_subscriber_error=fail_hook)
    bus.subscribe(BUILD, fail_subscriber)

    with caplog.at_level(logging.ERROR):
        bus.publish(BUILD)

    assert "error hook failed" in caplog.text


def test_publish_moves_a_watched_version_without_subscribers() -> None:
    bus = LocalTopicBus()
    with observe_reads() as observation:
        watch(bus.topic(BUILD.kind, BUILD.key))
    source, version = next(iter(observation.sources.items()))

    bus.publish(BUILD)

    assert source.validated_version() != version


def test_subscriptions_keeps_committed_and_one_staged_set() -> None:
    bus = LocalTopicBus()
    seen: list[BusAddress] = []
    build = bus.topic(BUILD.kind, BUILD.key)
    other = bus.topic(OTHER.kind, OTHER.key)
    subscriptions = Subscriptions(seen.append)

    subscriptions.stage((build, build))
    assert subscriptions.subscribed_addresses == (build,)
    subscriptions.commit()

    subscriptions.stage((other,))
    assert subscriptions.subscribed_addresses == (build, other)
    with pytest.raises(RuntimeError, match="already staged"):
        subscriptions.stage((build,))
    subscriptions.discard()

    assert subscriptions.committed == (build,)
    assert subscriptions.subscribed_addresses == (build,)
    bus.publish(BUILD, OTHER)
    assert seen == [build]


def test_subscriptions_commit_promotes_and_close_releases() -> None:
    bus = LocalTopicBus()
    build = bus.topic(BUILD.kind, BUILD.key)
    other = bus.topic(OTHER.kind, OTHER.key)
    subscriptions = Subscriptions(lambda _address: None)
    subscriptions.stage((build,))
    subscriptions.commit()
    subscriptions.stage((other,))
    subscriptions.commit()

    assert subscriptions.committed == (other,)
    assert subscriptions.subscribed_addresses == (other,)
    subscriptions.close()
    subscriptions.close()
    assert bus.snapshot().topics == ()


def test_subscriptions_unwinds_a_partial_stage() -> None:
    class BrokenBus(LocalTopicBus):
        @override
        def subscribe[AddressT: BusAddress](
            self, address: AddressT, callback: Callable[[AddressT], None]
        ) -> Callable[[], None]:
            if address == OTHER:
                raise RuntimeError("cannot subscribe")
            return super().subscribe(address, callback)

    bus = BrokenBus()
    build = bus.topic(BUILD.kind, BUILD.key)
    other = bus.topic(OTHER.kind, OTHER.key)
    subscriptions = Subscriptions(lambda _address: None)

    with pytest.raises(RuntimeError, match="cannot subscribe"):
        subscriptions.stage((build, other))

    assert subscriptions.staged is None
    assert subscriptions.subscribed_addresses == ()
    assert bus.snapshot().topics == ()


def test_subscriptions_reject_unresolved_names_without_replacing_current_dependencies() -> None:
    bus = LocalTopicBus()
    topic = bus.topic(BUILD.kind, BUILD.key)
    seen: list[BusAddress] = []
    subscriptions = Subscriptions(seen.append)
    subscriptions.stage((topic,))
    subscriptions.commit()

    with pytest.raises(TypeError, match="resolve topic names"):
        subscriptions.stage((cast(Any, OTHER),))

    assert subscriptions.staged is None
    assert subscriptions.committed == (topic,)
    topic.publish()
    assert seen == [topic]


def test_delivery_skips_removed_callbacks_and_defers_new_subscriptions() -> None:
    bus = LocalTopicBus()
    seen: list[str] = []

    def replace_subscriber(_address: BusAddress) -> None:
        seen.append("first")
        unsubscribe_first()
        unsubscribe_second()
        bus.subscribe(BUILD, lambda _: seen.append("new"))

    unsubscribe_first = bus.subscribe(BUILD, replace_subscriber)
    unsubscribe_second = bus.subscribe(BUILD, lambda _: seen.append("removed"))
    bus.subscribe(BUILD, lambda _: seen.append("last"))

    bus.publish(BUILD)
    assert seen == ["first", "last"]
    bus.publish(BUILD)
    assert seen == ["first", "last", "last", "new"]
    assert bus.snapshot().delivered == 4


def test_duplicate_callbacks_have_independent_idempotent_subscriptions() -> None:
    bus = LocalTopicBus()
    seen: list[BusAddress] = []
    callback = seen.append
    unsubscribe_first = bus.subscribe(BUILD, callback)
    unsubscribe_second = bus.subscribe(BUILD, callback)

    unsubscribe_second()
    unsubscribe_second()
    bus.publish(BUILD)
    assert seen == [BUILD]
    assert bus.snapshot().topics[0].subscribers == 1

    unsubscribe_first()
    bus.subscribe(BUILD, callback)
    unsubscribe_first()
    bus.publish(BUILD)
    assert seen == [BUILD, BUILD]


def test_handles_are_canonical_only_within_their_bus() -> None:
    first, second = LocalTopicBus(), LocalTopicBus()
    handle = first.topic("build", "42")
    assert first.topic("build", "42") is handle
    assert second.topic("build", "42") is not handle
    with observe_reads() as observation:
        first.topic("build", "42")
    assert not observation.sources


def test_handles_follow_their_owner_without_an_explicit_bus() -> None:
    first, second = LocalTopicBus(), LocalTopicBus()
    handle = first.topic("build", "42")
    with observe_reads() as observation:
        handle.watch()
    source, version = next(iter(observation.sources.items()))
    seen: list[BusAddress] = []
    subscriptions = Subscriptions(seen.append)
    subscriptions.stage(observation.addresses())
    subscriptions.commit()

    second.publish(handle.name)
    assert source.validated_version() == version
    assert seen == []
    first.publish(handle.name)
    assert source.validated_version() != version
    assert seen == [handle]
    handle.publish()
    assert seen == [handle, handle]
    subscriptions.close()
    handle.publish()
    assert seen == [handle, handle]


def test_a_handle_subscription_keeps_the_watched_generation_alive() -> None:
    bus = LocalTopicBus()
    handle = bus.topic("build", "42")
    seen: list[BusAddress] = []
    unsubscribe = handle.subscribe(seen.append)
    handle.publish()
    assert seen == [handle]
    unsubscribe()
    unsubscribe()
    handle.publish()
    assert seen == [handle]

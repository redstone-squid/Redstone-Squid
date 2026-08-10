"""Queue-health metric projection tests."""

from pytest_mock import MockerFixture

from squid.worker import queue_health
from squid.worker.queue_health import QueueHealthSnapshot, emit_queue_health


def test_queue_health_emits_current_depth_age_and_dead_letters(mocker: MockerFixture) -> None:
    gauge = mocker.patch.object(queue_health, "record_gauge")

    emit_queue_health(QueueHealthSnapshot("search_embeddings", 4, 2, 1, 37.5))

    attributes = {"squid.queue.name": "search_embeddings"}
    assert gauge.call_args_list == [
        mocker.call("squid.queue.ready", 4, attributes=attributes),
        mocker.call("squid.queue.in_flight", 2, attributes=attributes),
        mocker.call("squid.queue.dead_letters", 1, attributes=attributes),
        mocker.call("squid.queue.oldest_ready_age", 37.5, attributes=attributes),
    ]

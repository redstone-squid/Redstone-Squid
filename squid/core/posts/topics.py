"""Resource addresses used to refresh posts after their data changes."""

from typing import Protocol

from squid.core.posts.domain import ResourceKind
from squid_reactivity import TopicName
from squid_reactivity.topics import BusAddress


def resource_topic(resource_kind: ResourceKind, resource_key: str) -> TopicName:
    """Return a topic for an application resource.

    The ResourceKind annotation lets type checkers catch unknown resource kinds;
    constructing TopicName directly would accept any string.
    """
    return TopicName(resource_kind, resource_key)


class TopicPublisher(Protocol):
    """Publish resource changes through either a local bus or a PostgreSQL bridge."""

    def publish(self, *topics: BusAddress) -> None: ...

"""Resource addresses used to refresh panels after a build or other resource changes.

The bot, worker, and API can all change the data a panel displays. PostgreSQL
LISTEN/NOTIFY carries resource addresses between their local topic buses.
Notifications carry no resource data; subscribers read the current value from
the database when they refresh.
"""

from typing import Protocol

import asyncpg

from squid.config import DatabaseConfig
from squid.persistence.wake_listener import asyncpg_dsn
from squid.posts.domain import ResourceKind
from squid_reactivity import Address, Topic, TopicBus
from squid_storage import PostgresTopicBridge

RESOURCE_TOPIC_CHANNEL = "squid_resource_topics"
"""The PostgreSQL channel every Squid process shares."""


def resource_topic(resource_kind: ResourceKind, resource_key: str) -> Topic:
    """Return a topic for an application resource.

    The ResourceKind annotation lets type checkers catch unknown resource kinds;
    constructing Topic directly would accept any string.
    """
    return Topic(resource_kind, resource_key)


class TopicPublisher(Protocol):
    """Publish resource changes through either a local bus or a PostgreSQL bridge."""

    def publish(self, *topics: Address) -> None: ...


async def open_topic_bridge(database: DatabaseConfig, bus: TopicBus) -> PostgresTopicBridge | None:
    """Create a bridge, or return None if the database has no listener URL.

    LISTEN needs a connection that stays attached to one PostgreSQL session, so
    the pool uses the direct listener URL. The caller must run `bridge.run()` in
    a supervised task, cancel that task on shutdown, and close `bridge.pool`.
    Creating the bridge alone does not start sending or receiving notifications.

    Notifications can be missed. Consumers still need a way to refresh without
    them, such as the post reconciler's periodic poll.
    """
    if database.listener_url is None:
        return None
    pool = await asyncpg.create_pool(asyncpg_dsn(database.listener_url), min_size=1, max_size=2)
    return PostgresTopicBridge(pool, bus, channel=RESOURCE_TOPIC_CHANNEL)

"""Carry resource refresh notifications between processes with PostgreSQL LISTEN/NOTIFY."""

import asyncpg

from squid.config import DatabaseConfig
from squid.core.persistence.wake_listener import asyncpg_dsn
from squid_reactivity import TopicBus
from squid_storage import PostgresTopicBridge

RESOURCE_TOPIC_CHANNEL = "squid_resource_topics"
"""The PostgreSQL channel every Squid process shares."""


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

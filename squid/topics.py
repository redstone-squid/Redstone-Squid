"""The resource topic vocabulary, and the bridge that carries it between processes.

`TopicBus` is process-local, but three processes write what a panel shows: the bot, the worker
that finishes a schematic render, and the API. The address a panel follows therefore travels
over PostgreSQL `LISTEN`/`NOTIFY`; only the address travels, so every subscriber re-reads the
database exactly as it does for a local publish.
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
    """Address one bot-owned resource consistently across publishers and subscribers.

    A bare `Topic` accepts any kind string; this constructor accepts only a `ResourceKind`.
    """
    return Topic(resource_kind, resource_key)


class TopicPublisher(Protocol):
    """What a process publishes through: the local bus, or the bridge in front of it."""

    def publish(self, *topics: Address) -> None:
        """Notify each address's local subscribers in order, synchronously and before returning.

        A bridge additionally queues the address for the other processes, without waiting for it
        to leave. No implementation carries state, so a subscriber always re-reads the database.
        """
        ...


async def open_topic_bridge(database: DatabaseConfig, bus: TopicBus) -> PostgresTopicBridge | None:
    """Join the shared resource channel, or return `None` when `database.listener_url` is unset.

    `LISTEN` needs a session-level connection, so this reuses the direct URL the domain event and
    permission epoch listeners already require. Without it the process keeps its local bus and
    cross-process freshness falls back to the reconciler's poll. The caller owns the returned
    bridge: run it, and close `bridge.pool` on shutdown.
    """
    if database.listener_url is None:
        return None
    pool = await asyncpg.create_pool(asyncpg_dsn(database.listener_url), min_size=1, max_size=2)
    return PostgresTopicBridge(pool, bus, channel=RESOURCE_TOPIC_CHANNEL)

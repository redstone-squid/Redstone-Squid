"""The resource topic vocabulary, and the bridge that carries it between processes.

`TopicBus` is process-local, but the writes a panel shows are made by three processes: the
bot itself, the worker that finishes a schematic render, and the API. The address a panel
follows therefore travels over PostgreSQL `LISTEN`/`NOTIFY`. Only the address travels --
every subscriber re-reads the database, exactly as it does for a local publish.
"""

from typing import Protocol, cast, get_args

import asyncpg

import squid_layouts as sl
from squid.config import DatabaseConfig
from squid.persistence.wake_listener import asyncpg_dsn
from squid.posts.domain import ResourceKind
from squid_layouts.discord.durability import PostgresTopicBridge

type ResourceTopic = tuple[ResourceKind, str]

RESOURCE_TOPIC_CHANNEL = "squid_resource_topics"
"""The PostgreSQL channel every Squid process shares."""

_RESOURCE_KINDS = frozenset(get_args(ResourceKind))


def resource_topic(resource_kind: ResourceKind, resource_key: str) -> ResourceTopic:
    """Address one bot-owned resource consistently across publishers and subscribers."""
    return resource_kind, resource_key


class TopicPublisher(Protocol):
    """What a process publishes through: the local bus, or the bridge in front of it."""

    def publish(self, *topics: sl.Topic) -> None: ...


class ResourceTopicCodec:
    """Carry `ResourceTopic` as ``kind:key``; no other address crosses a process boundary.

    An address the codec does not recognise -- a `Shared` cell identity, a panel-private
    tuple -- is published locally and never named on the wire, which is what keeps the
    channel a vocabulary rather than a leak of whatever anyone happened to publish.
    """

    def encode(self, topic: sl.Topic) -> str | None:
        match topic:
            case (str() as kind, str() as key) if kind in _RESOURCE_KINDS and key:
                return f"{kind}:{key}"
            case _:
                return None

    def decode(self, text: str) -> sl.Topic | None:
        kind, separator, key = text.partition(":")
        if not separator or not key or kind not in _RESOURCE_KINDS:
            return None
        return resource_topic(cast(ResourceKind, kind), key)


async def open_topic_bridge(database: DatabaseConfig, bus: sl.TopicBus) -> PostgresTopicBridge | None:
    """Join the shared resource channel, or return `None` when none is configured.

    `LISTEN` needs a session-level connection, so this reuses the direct URL the domain
    event and permission epoch listeners already require. Without it the process keeps its
    local bus, and cross-process freshness falls back to the reconciler's poll. The caller
    owns the returned bridge: run it, and close `bridge.pool` on shutdown.
    """
    if database.listener_url is None:
        return None
    pool = await asyncpg.create_pool(asyncpg_dsn(database.listener_url), min_size=1, max_size=2)
    return PostgresTopicBridge(pool, bus, ResourceTopicCodec(), channel=RESOURCE_TOPIC_CHANNEL)

"""Cross-process topic delivery over a real PostgreSQL LISTEN/NOTIFY channel."""

import uuid
from functools import partial
from typing import Any, cast

import anyio
import asyncpg
import pytest
from testcontainers.postgres import PostgresContainer

from squid.topics import resource_topic
from squid_layouts import Topic, TopicBus
from squid_layouts.discord.durability import PostgresTopicBridge


async def _announce(event: anyio.Event) -> None:
    event.set()


async def test_two_processes_exchange_topics_over_one_channel(postgres_container: PostgresContainer) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    channel = f"topic_bridge_{uuid.uuid4().hex}"
    here_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    there_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    assert here_pool is not None
    assert there_pool is not None
    here, there = TopicBus(), TopicBus()
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    async def record_here(topic: Topic) -> None:
        seen_here.append(topic)

    async def record_there(topic: Topic) -> None:
        seen_there.append(topic)

    here.subscribe(resource_topic("build", "42"), record_here)
    there.subscribe(resource_topic("build", "42"), record_there)
    listening_here, listening_there = anyio.Event(), anyio.Event()
    bridge_here = PostgresTopicBridge(here_pool, here, channel=channel, on_resync=partial(_announce, listening_here))
    bridge_there = PostgresTopicBridge(
        there_pool, there, channel=channel, on_resync=partial(_announce, listening_there)
    )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(bridge_here.run)
            tasks.start_soon(bridge_there.run)
            tasks.start_soon(here.run)
            tasks.start_soon(there.run)
            with anyio.fail_after(30):
                await listening_here.wait()
                await listening_there.wait()

                bridge_here.publish(resource_topic("build", "42"))
                while not seen_there:
                    await anyio.sleep(0.05)
                # The publisher's own connection receives the notification too; dropping it
                # is the only thing keeping this off a loop.
                while bridge_here.snapshot().ignored < 1:
                    await anyio.sleep(0.05)

                bridge_there.publish(resource_topic("build", "42"))
                while len(seen_here) < 2:
                    await anyio.sleep(0.05)
            tasks.cancel_scope.cancel()
    finally:
        await here_pool.close()
        await there_pool.close()

    # One local publish plus one remote, each way.
    assert seen_here == [resource_topic("build", "42")] * 2
    assert seen_there == [resource_topic("build", "42")] * 2
    assert bridge_there.snapshot().received == 1
    assert bridge_here.snapshot().received == 1


async def test_transaction_publish_is_commit_ordered_and_self_delivered(
    postgres_container: PostgresContainer,
) -> None:
    dsn = postgres_container.get_connection_url(driver="asyncpg").replace("postgresql+asyncpg://", "postgresql://")
    channel = f"topic_bridge_{uuid.uuid4().hex}"
    here_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    there_pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    assert here_pool is not None
    assert there_pool is not None
    here, there = TopicBus(), TopicBus()
    committed = resource_topic("build", "committed")
    rolled_back = resource_topic("build", "rolled-back")
    seen_here: list[Topic] = []
    seen_there: list[Topic] = []

    async def record_here(topic: Topic) -> None:
        seen_here.append(topic)

    async def record_there(topic: Topic) -> None:
        seen_there.append(topic)

    here.subscribe(committed, cast(Any, record_here))
    here.subscribe(rolled_back, cast(Any, record_here))
    there.subscribe(committed, cast(Any, record_there))
    there.subscribe(rolled_back, cast(Any, record_there))
    listening_here, listening_there = anyio.Event(), anyio.Event()
    bridge_here = PostgresTopicBridge(here_pool, here, channel=channel, on_resync=partial(_announce, listening_here))
    bridge_there = PostgresTopicBridge(
        there_pool, there, channel=channel, on_resync=partial(_announce, listening_there)
    )

    try:
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(bridge_here.run)
            tasks.start_soon(bridge_there.run)
            tasks.start_soon(here.run)
            tasks.start_soon(there.run)
            with anyio.fail_after(30):
                await listening_here.wait()
                await listening_there.wait()

                async with here_pool.acquire() as connection, connection.transaction():
                    await bridge_here.publish_in(connection, committed)
                    assert seen_here == []
                    assert seen_there == []

                while seen_here != [committed] or seen_there != [committed]:
                    await anyio.sleep(0.05)

                async def rollback() -> None:
                    async with here_pool.acquire() as connection, connection.transaction():
                        await bridge_here.publish_in(connection, rolled_back)
                        raise RuntimeError("rollback")

                with pytest.raises(RuntimeError, match="rollback"):
                    await rollback()
                await anyio.sleep(0.1)
            tasks.cancel_scope.cancel()
    finally:
        await here_pool.close()
        await there_pool.close()

    assert seen_here == [committed]
    assert seen_there == [committed]

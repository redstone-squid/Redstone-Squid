"""Cross-process topic delivery over a real PostgreSQL LISTEN/NOTIFY channel."""

import uuid
from functools import partial

import anyio
import asyncpg
from testcontainers.postgres import PostgresContainer

from squid.topics import ResourceTopicCodec, resource_topic
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
    bridge_here = PostgresTopicBridge(
        here_pool, here, ResourceTopicCodec(), channel=channel, on_resync=partial(_announce, listening_here)
    )
    bridge_there = PostgresTopicBridge(
        there_pool, there, ResourceTopicCodec(), channel=channel, on_resync=partial(_announce, listening_there)
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

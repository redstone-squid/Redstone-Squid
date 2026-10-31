"""PostgreSQL wake hints survive disconnects and release their owned connection."""

import asyncio
import uuid

import anyio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from squid.events.infrastructure.listener import CHANNEL, DomainEventWakeListener


async def test_listener_delivers_commit_hints_reconnects_and_closes_on_cancellation(async_engine: AsyncEngine) -> None:
    application_name = f"squid-listener-test-{uuid.uuid4().hex}"
    url = async_engine.url.update_query_dict({"application_name": application_name})
    listener = DomainEventWakeListener(SecretStr(url.render_as_string(hide_password=False)), reconnect_seconds=0)
    wakes: asyncio.Queue[None] = asyncio.Queue()

    async def process_events() -> None:
        wakes.put_nowait(None)

    async def next_wake() -> None:
        with anyio.fail_after(5):
            await wakes.get()

    async with async_engine.connect() as connection:
        admin = await connection.execution_options(isolation_level="AUTOCOMMIT")

        async def backend_pid() -> int | None:
            return await admin.scalar(
                text("SELECT pid FROM pg_stat_activity WHERE application_name = :name"),
                {"name": application_name},
            )

        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(listener.run, process_events)
                await next_wake()
                original_pid = await backend_pid()
                assert original_pid is not None

                async with async_engine.begin() as publisher:
                    await publisher.execute(text("SELECT pg_notify(:channel, 'committed')"), {"channel": CHANNEL})
                    assert wakes.empty()
                await next_wake()

                assert await admin.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": original_pid})
                await next_wake()
                reconnected_pid = await backend_pid()
                assert reconnected_pid is not None
                assert reconnected_pid != original_pid

                await admin.execute(text("SELECT pg_notify(:channel, 'reconnected')"), {"channel": CHANNEL})
                await next_wake()
                tasks.cancel_scope.cancel()

            with anyio.fail_after(5):
                while await backend_pid() is not None:
                    await anyio.sleep(0.01)
        finally:
            await admin.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = :name"),
                {"name": application_name},
            )

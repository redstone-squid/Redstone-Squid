"""PostgreSQL LISTEN wake-hint contracts."""

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock

import anyio
import pytest
from pydantic import SecretStr

from squid.events.infrastructure.listener import DomainEventWakeListener
from squid.persistence import wake_listener as listener_module


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False
        self.notification: Callable[..., None] | None = None
        self.terminated: Callable[..., None] | None = None

    async def add_listener(self, _channel: str, callback: Callable[..., None]) -> None:
        self.notification = callback

    def add_termination_listener(self, callback: Callable[..., None]) -> None:
        self.terminated = callback

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


async def test_listener_processes_startup_and_commit_wake_hints(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = FakeConnection()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(listener_module.asyncpg, "connect", connect)
    listener = DomainEventWakeListener(SecretStr("postgresql+asyncpg://user:password@database.example/squid"))
    processed = 0
    first = asyncio.Event()
    second = asyncio.Event()

    async def process_events() -> None:
        nonlocal processed
        processed += 1
        (first if processed == 1 else second).set()

    task = asyncio.create_task(listener.run(process_events))
    await asyncio.wait_for(first.wait(), timeout=1)
    assert connection.notification is not None
    connection.notification(connection, 1, "squid_domain_events", "42")
    await asyncio.wait_for(second.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    connect.assert_awaited_once_with("postgresql://user:password@database.example/squid")
    assert connection.closed is True


async def test_listener_reconnects_after_postgres_terminates_the_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_connection = FakeConnection()
    second_connection = FakeConnection()
    connect = AsyncMock(side_effect=[first_connection, second_connection])
    monkeypatch.setattr(listener_module.asyncpg, "connect", connect)
    listener = DomainEventWakeListener(
        SecretStr("postgresql+asyncpg://user:password@database.example/squid"),
        reconnect_seconds=0,
    )
    processed = 0
    started = asyncio.Event()
    reconnected = asyncio.Event()

    async def process_events() -> None:
        nonlocal processed
        processed += 1
        (started if processed == 1 else reconnected).set()

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(listener.run, process_events)
        await asyncio.wait_for(started.wait(), timeout=1)
        assert first_connection.terminated is not None
        first_connection.closed = True
        first_connection.terminated(first_connection)
        await asyncio.wait_for(reconnected.wait(), timeout=1)
        tasks.cancel_scope.cancel()

    assert connect.await_count == 2
    assert first_connection.closed is True
    assert second_connection.closed is True

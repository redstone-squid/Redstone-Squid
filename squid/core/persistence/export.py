"""Bound every query for a download to one PostgreSQL snapshot."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.core.errors import ServiceUnavailableError


@asynccontextmanager
async def export_snapshot(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    try:
        with anyio.fail_after(30):
            async with sessions.begin() as session:
                await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
                await session.execute(text("SET LOCAL TIME ZONE 'UTC'"))
                yield session
    except TimeoutError as exc:
        msg = "Export exceeded the generation time limit."
        raise ServiceUnavailableError(msg) from exc

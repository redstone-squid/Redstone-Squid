"""PostgreSQL lifecycle coverage for durable schematic render projection."""

from collections.abc import AsyncGenerator
from typing import Any, cast

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.schematics.application import (
    RenderPreparation,
    RenderSkipReason,
    SchematicRenderJobService,
    SkippedRender,
)
from squid.schematics.infrastructure.render_jobs import PostgresSchematicRenderJobRepository
from squid.worker.rendering import SchematicRenderProjector

pytestmark = pytest.mark.asyncio

_TABLE = Base.metadata.tables["schematic_render_queue"]


@pytest.fixture
async def render_queue(async_engine: AsyncEngine) -> AsyncGenerator[None]:
    async with async_engine.begin() as connection:
        await connection.execute(text("CREATE TABLE builds (id BIGINT PRIMARY KEY)"))
        await connection.run_sync(Base.metadata.create_all, tables=[_TABLE])
        await connection.execute(text("INSERT INTO builds (id) VALUES (7)"))
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=[_TABLE])
            await connection.execute(text("DROP TABLE builds"))


class PreparedSchematics:
    def __init__(self, outcome: RenderPreparation | Exception) -> None:
        self.outcome = outcome

    async def prepare_render(self, _build_id: int) -> RenderPreparation:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


async def _enqueue(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory.begin() as session:
        await session.execute(text("INSERT INTO schematic_render_queue (build_id) VALUES (7)"))


async def _row(session_factory: async_sessionmaker[AsyncSession]) -> tuple[Any, ...] | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT attempts, claimed_at, dead_at IS NOT NULL, available_at > now(), last_error "
                    "FROM schematic_render_queue WHERE build_id = 7"
                )
            )
        ).one_or_none()
        return None if row is None else tuple(row)


def _projector(
    session_factory: async_sessionmaker[AsyncSession],
    outcome: RenderPreparation | Exception,
) -> SchematicRenderProjector:
    jobs = SchematicRenderJobService(PostgresSchematicRenderJobRepository(session_factory), max_attempts=2)
    return SchematicRenderProjector(
        jobs,
        cast(Any, PreparedSchematics(outcome)),
        cast(Any, object()),
        "https://api.example",
        enabled=True,
    )


async def test_a_permanent_render_skip_acknowledges_and_removes_the_intent(
    render_queue: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del render_queue
    await _enqueue(async_session_factory)
    projector = _projector(
        async_session_factory,
        SkippedRender(RenderSkipReason.NO_PRIMARY_SCHEMATIC),
    )

    await projector.process_batch()

    assert await _row(async_session_factory) is None


async def test_a_transient_render_failure_backs_off_then_is_retained_as_a_dead_letter(
    render_queue: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del render_queue
    await _enqueue(async_session_factory)
    projector = _projector(async_session_factory, RuntimeError("renderer unavailable"))

    await projector.process_batch()

    assert await _row(async_session_factory) == (1, None, False, True, "renderer unavailable")

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE schematic_render_queue SET available_at = now() WHERE build_id = 7"))
    await projector.process_batch()

    assert await _row(async_session_factory) == (2, None, True, False, "renderer unavailable")

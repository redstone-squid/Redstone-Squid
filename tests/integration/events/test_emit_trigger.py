"""Coverage for the SQL that turns row transitions into domain events."""

from collections.abc import AsyncGenerator, Sequence

import pytest
from alembic_utils.pg_function import PGFunction
from alembic_utils.pg_trigger import PGTrigger
from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.sql.elements import TextClause

from squid.persistence.alembic_entities import ALEMBIC_UTIL_ENTITIES

_CREATE_SCHEMA = """
CREATE TABLE builds (
    id BIGSERIAL PRIMARY KEY,
    submission_status SMALLINT NOT NULL,
    submitter_account_id BIGINT NOT NULL DEFAULT 1,
    category TEXT
);
CREATE TABLE vote_sessions (
    id BIGSERIAL PRIMARY KEY,
    status VARCHAR NOT NULL,
    result VARCHAR NOT NULL DEFAULT 'pending',
    kind VARCHAR NOT NULL
);
CREATE TABLE domain_events (
    id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    schema_version SMALLINT NOT NULL DEFAULT 1,
    aggregate_kind TEXT NOT NULL,
    aggregate_id BIGINT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE domain_event_consumers (
    name TEXT PRIMARY KEY
);
CREATE TABLE domain_event_deliveries (
    event_id BIGINT REFERENCES domain_events(id) ON DELETE CASCADE,
    consumer TEXT REFERENCES domain_event_consumers(name) ON DELETE CASCADE,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    claimed_at TIMESTAMPTZ,
    claim_token UUID,
    claim_count INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    PRIMARY KEY (event_id, consumer)
);
INSERT INTO domain_event_consumers (name) VALUES ('discord');
"""

_DROP_SCHEMA = """
DROP TABLE IF EXISTS
    domain_event_deliveries, domain_event_consumers, domain_events, vote_sessions, builds CASCADE;
DROP FUNCTION IF EXISTS public.emit_domain_event() CASCADE;
DROP FUNCTION IF EXISTS public.publish_domain_event(text, integer, text, bigint, jsonb) CASCADE;
"""

_ENTITY_NAMES = {
    "publish_domain_event",
    "emit_domain_event",
    "builds_emit_domain_event",
    "vote_sessions_emit_domain_event",
}


def _managed_sql() -> list[TextClause]:
    """Return the real function and trigger definitions this test exercises."""
    functions = [
        text(entity.to_sql_statement_create().text)
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, PGFunction) and entity.signature.partition("(")[0] in _ENTITY_NAMES
    ]
    triggers = [
        text(entity.to_sql_statement_create().text)
        for entity in ALEMBIC_UTIL_ENTITIES
        if isinstance(entity, PGTrigger) and entity.signature.partition("(")[0] in _ENTITY_NAMES
    ]
    assert len(functions) == 2
    assert len(triggers) == 2
    return [*functions, *triggers]


@pytest.fixture(autouse=True)
async def event_schema(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    """Create the minimal production-shaped schema plus the managed emit entities."""
    async with async_engine.begin() as connection:
        for statement in _CREATE_SCHEMA.strip().split(";"):
            if statement.strip():
                await connection.execute(text(statement))
        for statement in _managed_sql():
            await connection.execute(statement)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            for statement in _DROP_SCHEMA.strip().split(";"):
                if statement.strip():
                    await connection.execute(text(statement))


async def _events(session_factory: async_sessionmaker[AsyncSession]) -> Sequence[Row[tuple[str, str, int]]]:
    async with session_factory() as session:
        return (
            await session.execute(
                text("SELECT event_type, aggregate_kind, aggregate_id FROM domain_events ORDER BY id")
            )
        ).all()


async def _seed_build(session_factory: async_sessionmaker[AsyncSession], status: int = 0) -> int:
    async with session_factory.begin() as session:
        return (
            await session.execute(
                text("INSERT INTO builds (submission_status) VALUES (:status) RETURNING id"), {"status": status}
            )
        ).scalar_one()


async def test_confirming_a_build_emits_one_event_with_a_delivery_per_consumer(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_id = await _seed_build(async_session_factory)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET submission_status = 1 WHERE id = :id"), {"id": build_id})

    assert await _events(async_session_factory) == [
        ("build.submitted", "build", build_id),
        ("build.confirmed", "build", build_id),
    ]
    async with async_session_factory() as session:
        deliveries = (await session.execute(text("SELECT consumer, attempts FROM domain_event_deliveries"))).all()
        confirmed = (
            await session.execute(
                text(
                    "SELECT schema_version, payload FROM domain_events "
                    "WHERE event_type = 'build.confirmed' AND aggregate_id = :build_id"
                ),
                {"build_id": build_id},
            )
        ).one()
    assert deliveries == [("discord", 0), ("discord", 0)]
    assert confirmed.schema_version == 3
    assert confirmed.payload["first_confirmation"] is True
    assert confirmed.payload["submitter_account_id"] == 1


async def test_denying_a_build_emits_a_denied_event(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_id = await _seed_build(async_session_factory)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET submission_status = 2 WHERE id = :id"), {"id": build_id})

    assert await _events(async_session_factory) == [
        ("build.submitted", "build", build_id),
        ("build.denied", "build", build_id),
    ]


async def test_rewriting_the_same_status_emits_nothing(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Confirming an already-confirmed build must not re-post it."""
    build_id = await _seed_build(async_session_factory, status=1)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET submission_status = 1 WHERE id = :id"), {"id": build_id})

    assert await _events(async_session_factory) == [("build.submitted", "build", build_id)]


async def test_returning_a_build_to_pending_emits_nothing(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_id = await _seed_build(async_session_factory, status=1)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET submission_status = 0 WHERE id = :id"), {"id": build_id})

    assert await _events(async_session_factory) == [("build.submitted", "build", build_id)]


async def test_inserting_a_build_emits_a_submitted_event(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    build_id = await _seed_build(async_session_factory, status=1)
    assert await _events(async_session_factory) == [("build.submitted", "build", build_id)]


async def test_closing_a_vote_session_emits_one_event(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        session_id = (
            await session.execute(
                text("INSERT INTO vote_sessions (status, kind) VALUES ('open', 'build') RETURNING id")
            )
        ).scalar_one()

    async with async_session_factory.begin() as session:
        await session.execute(
            text("UPDATE vote_sessions SET status = 'closed', result = 'approved' WHERE id = :id"), {"id": session_id}
        )
        # A second write while already closed must not emit again.
        await session.execute(text("UPDATE vote_sessions SET status = 'closed' WHERE id = :id"), {"id": session_id})

    assert await _events(async_session_factory) == [("vote_session.closed", "vote_session", session_id)]
    async with async_session_factory() as session:
        payload = (await session.execute(text("SELECT payload FROM domain_events"))).scalar_one()
    assert payload == {"kind": "build", "result": "approved"}


async def test_no_registered_consumers_records_the_event_without_deliveries(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        await session.execute(text("DELETE FROM domain_event_consumers"))
    build_id = await _seed_build(async_session_factory)

    async with async_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET submission_status = 1 WHERE id = :id"), {"id": build_id})

    assert len(await _events(async_session_factory)) == 2
    async with async_session_factory() as session:
        assert (await session.execute(text("SELECT count(*) FROM domain_event_deliveries"))).scalar_one() == 0


async def test_publisher_records_the_schema_version(
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with async_session_factory.begin() as session:
        event_id = (
            await session.execute(
                text("SELECT public.publish_domain_event('example.changed', 2, 'example', 7, '{\"value\": 1}')")
            )
        ).scalar_one()

    async with async_session_factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT event_type, schema_version, aggregate_kind, aggregate_id, payload "
                    "FROM domain_events WHERE id = :event_id"
                ),
                {"event_id": event_id},
            )
        ).one()
    assert row == ("example.changed", 2, "example", 7, {"value": 1})

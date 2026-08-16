"""Claim, fencing, and backoff semantics shared by the outbox queues.

Every case runs against the migrated schema rather than hand-written DDL. The
hand-written table used to omit the triggers the real `discord_sync_queue` carries,
which is precisely the class of regression these tests exist to catch.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.sync.application import ReconciliationJob
from squid.sync.infrastructure.repository import PostgresDiscordSyncQueue


async def _seed_sync_job(session_factory: async_sessionmaker[AsyncSession], source_key: str = "1") -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', :key)"),
            {"key": source_key},
        )


async def _count(session_factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with session_factory() as session:
        return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def test_claiming_stamps_a_token_and_completing_removes_the_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)

    (job,) = await queue.claim(limit=10)
    assert job.claimed_at is not None
    assert await queue.complete(job) is True
    assert await _count(migrated_session_factory, "discord_sync_queue") == 0


async def test_a_stale_claim_token_cannot_delete_a_requeued_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A trigger firing mid-job resets claimed_at, which must invalidate the claim."""
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)
    (job,) = await queue.claim(limit=10)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE discord_sync_queue SET claimed_at = NULL"))

    assert await queue.complete(job) is False
    assert await _count(migrated_session_factory, "discord_sync_queue") == 1


async def test_a_claimed_row_is_not_handed_to_a_second_worker(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)

    assert len(await queue.claim(limit=10)) == 1
    assert await queue.claim(limit=10) == ()


async def test_a_claim_abandoned_past_the_visibility_timeout_is_reclaimed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)
    await queue.claim(limit=10)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE discord_sync_queue SET claimed_at = now() - interval '6 minutes'"))

    assert len(await queue.claim(limit=10)) == 1


async def test_failing_backs_the_row_off_instead_of_dropping_it(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)
    (job,) = await queue.claim(limit=10)

    assert await queue.fail(job, "boom", max_attempts=8) is False
    async with migrated_session_factory() as session:
        attempts, claimed_at, future = (
            await session.execute(text("SELECT attempts, claimed_at, enqueued_at > now() FROM discord_sync_queue"))
        ).one()
    assert (attempts, claimed_at, future) == (1, None, True)
    # Backed-off work is not ready yet, so a drain right now must not pick it up.
    assert await queue.claim(limit=10) == ()


async def test_failing_at_the_attempt_ceiling_dead_letters_the_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = PostgresDiscordSyncQueue(migrated_session_factory)
    (job,) = await queue.claim(limit=10)
    ceiling_job = ReconciliationJob(
        id=job.id,
        resource_kind=job.resource_kind,
        source_key=job.source_key,
        action=job.action,
        generation=job.generation,
        attempts=7,
        claimed_at=job.claimed_at,
    )

    assert await queue.fail(ceiling_job, "boom", max_attempts=8) is True
    async with migrated_session_factory() as session:
        attempts, claimed_at, dead, error = (
            await session.execute(
                text("SELECT attempts, claimed_at, dead_at IS NOT NULL, last_error FROM discord_sync_queue")
            )
        ).one()
    assert (attempts, claimed_at, dead, error) == (8, None, True, "boom")
    assert await queue.claim(limit=10) == ()

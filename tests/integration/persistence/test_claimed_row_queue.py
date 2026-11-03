"""Claim, fencing, and backoff semantics shared by the outbox queues.

Every case runs against the migrated schema rather than hand-written DDL. The
hand-written table used to omit the triggers the real `discord_sync_queue` carries,
which is precisely the class of regression these tests exist to catch.
"""

import logging
from functools import partial
from typing import Any
from unittest.mock import patch

import pytest
from sqlalchemy import literal, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue, retry_delay, retry_delay_sql
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.schematics.infrastructure.jobs import SCHEMATIC_JOB_SPEC
from squid.sync.application import DiscordReconciliationService, ReconciliationJob
from squid.sync.infrastructure.repository import DISCORD_SYNC_QUEUE_SPEC
from tests.support.unit_of_work import transact


async def _seed_sync_job(session_factory: async_sessionmaker[AsyncSession], source_key: str = "1") -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text("INSERT INTO discord_sync_queue (resource_kind, source_key) VALUES ('build', :key)"),
            {"key": source_key},
        )


async def _count(session_factory: async_sessionmaker[AsyncSession], table: str) -> int:
    async with session_factory() as session:
        return (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


async def _row(session_factory: async_sessionmaker[AsyncSession], statement: str) -> Any:
    async with session_factory() as session:
        return (await session.execute(text(statement))).one()


async def test_claiming_stamps_a_token_and_completing_removes_the_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    assert job.claim_token is not None
    assert await transact(queue, lambda uow: uow.reconciliation.complete(job), commit=True) is True
    assert await _count(migrated_session_factory, "discord_sync_queue") == 0


async def test_each_claimed_row_gets_its_own_token(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`gen_random_uuid()` is volatile, so one UPDATE still mints a token per row."""
    for key in ("1", "2", "3"):
        await _seed_sync_job(migrated_session_factory, key)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    jobs = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    assert len({job.claim_token for job in jobs}) == 3

    async with migrated_session_factory() as session:
        stamped = (
            await session.execute(
                text("SELECT count(*) FROM discord_sync_queue WHERE claimed_at IS NOT NULL AND claim_token IS NOT NULL")
            )
        ).scalar_one()
    assert stamped == 3


async def test_a_reclaimed_row_cannot_be_completed_by_the_previous_holder(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (stale,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    await _expire_the_claim(migrated_session_factory)
    (fresh,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    assert fresh.claim_token != stale.claim_token

    assert await transact(queue, lambda uow: uow.reconciliation.complete(stale), commit=True) is False
    assert await _count(migrated_session_factory, "discord_sync_queue") == 1


async def test_a_reclaimed_row_cannot_be_failed_by_the_previous_holder(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (stale,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    await _expire_the_claim(migrated_session_factory)
    await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    assert (
        await transact(queue, lambda uow: uow.reconciliation.fail(stale, "boom", max_attempts=8), commit=True) is False
    )
    attempts, last_error, claimed = await _row(
        migrated_session_factory,
        "SELECT attempts, last_error, claim_token IS NOT NULL FROM discord_sync_queue",
    )
    # The attempts increment, the error text, and the new holder's claim all survive:
    # `attempts` is owned by `fail`, so a rejected one leaves it where the claim left it.
    assert (attempts, last_error, claimed) == (0, None, True)


async def test_a_lost_fence_is_logged_and_counted(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A queue that has begun doing its work twice must not look like a healthy one."""
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (stale,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    await _expire_the_claim(migrated_session_factory)
    await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    with (
        caplog.at_level(logging.WARNING, logger="squid.persistence.queue"),
        patch("squid.persistence.queue.add_counter") as counter,
    ):
        assert await transact(queue, lambda uow: uow.reconciliation.complete(stale), commit=True) is False

    assert "claim was already lost" in caplog.text
    counter.assert_called_once_with("squid.queue.lost_fences", attributes={"squid.queue.name": "discord_sync"})


async def test_a_claimed_row_is_not_handed_to_a_second_worker(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)

    assert len(await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)) == 1
    assert await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True) == ()


async def test_a_claim_abandoned_past_the_visibility_timeout_is_reclaimed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    await _expire_the_claim(migrated_session_factory)

    assert len(await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)) == 1


async def test_a_retry_backs_off_available_at_and_leaves_enqueued_at_alone(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Backoff must not rewrite when the work was requested.

    Overloading `enqueued_at` destroyed FIFO fairness and made a job that had been
    failing for an hour report as fresh to `squid.queue.oldest_ready_age`.
    """
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)

    assert await transact(queue, lambda uow: uow.reconciliation.fail(job, "boom", max_attempts=8), commit=True) is False
    attempts, claimed_at, token, backed_off, requested_now = await _row(
        migrated_session_factory,
        "SELECT attempts, claimed_at, claim_token, available_at > now(), enqueued_at <= now() FROM discord_sync_queue",
    )
    assert (attempts, claimed_at, token, backed_off, requested_now) == (1, None, None, True, True)
    # Backed-off work is not ready yet, so a drain right now must not pick it up.
    assert await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True) == ()


async def test_a_retry_leaves_the_sync_generation_alone(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A transient failure must not invalidate the message-projection fence.

    Backoff used to write `enqueued_at`, which a trigger watched to bump
    `generation`, so one failed Discord call re-rendered every projected message of
    the resource. The trigger is gone; this is the guard against a domain write
    creeping back onto the retry path.
    """
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    before = (await _row(migrated_session_factory, "SELECT generation FROM discord_sync_queue"))[0]

    await transact(queue, lambda uow: uow.reconciliation.fail(job, "boom", max_attempts=8), commit=True)

    after = (await _row(migrated_session_factory, "SELECT generation FROM discord_sync_queue"))[0]
    assert after == before == job.generation


async def test_a_reenqueue_clears_the_claim_and_the_backoff(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The enqueue trigger must reset the token, or a re-enqueued row keeps a stale fence."""
    async with migrated_session_factory.begin() as session:
        account_id = (await session.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id"))).scalar_one()
        session_id = (
            await session.execute(
                text(
                    "INSERT INTO vote_sessions (status, kind, author_account_id, pass_threshold, fail_threshold) "
                    "VALUES ('open', 'delete_log', :account, 3, -3) RETURNING id"
                ),
                {"account": account_id},
            )
        ).scalar_one()
        await session.execute(
            text(
                "INSERT INTO delete_log_vote_sessions "
                "(vote_session_id, target_message_id, target_channel_id, target_server_id) "
                "VALUES (:id, 1, 2, 3)"
            ),
            {"id": session_id},
        )
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    await transact(queue, lambda uow: uow.reconciliation.fail(job, "boom", max_attempts=8), commit=True)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE vote_sessions SET status = 'closed' WHERE id = :id"), {"id": session_id})

    claimed_at, token, ready = await _row(
        migrated_session_factory,
        "SELECT claimed_at, claim_token, available_at <= now() FROM discord_sync_queue",
    )
    assert (claimed_at, token, ready) == (None, None, True)
    assert await transact(queue, lambda uow: uow.reconciliation.fail(job, "boom", max_attempts=8), commit=True) is False


async def test_failing_at_the_attempt_ceiling_dead_letters_the_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _seed_sync_job(migrated_session_factory)
    queue = partial(SqlAlchemyUnitOfWork, migrated_session_factory)
    (job,) = await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True)
    ceiling_job = ReconciliationJob(
        id=job.id,
        resource_kind=job.resource_kind,
        source_key=job.source_key,
        action=job.action,
        generation=job.generation,
        attempts=7,
        claim_token=job.claim_token,
    )

    assert (
        await transact(queue, lambda uow: uow.reconciliation.fail(ceiling_job, "boom", max_attempts=8), commit=True)
        is True
    )
    attempts, claimed_at, token, dead, error = await _row(
        migrated_session_factory,
        "SELECT attempts, claimed_at, claim_token, dead_at IS NOT NULL, last_error FROM discord_sync_queue",
    )
    assert (attempts, claimed_at, token, dead, error) == (8, None, None, True, "boom")
    assert await transact(queue, lambda uow: uow.reconciliation.claim(limit=10), commit=True) == ()


async def test_a_caller_owned_claim_is_not_committed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """With `session=`, the claim joins the caller's transaction instead of ending it.

    The previous helper committed a session it did not own, which dropped the
    caller's row locks and excluded the one adapter that had a transaction worth
    joining.
    """
    await _seed_sync_job(migrated_session_factory)
    queue = ClaimedRowQueue(DISCORD_SYNC_QUEUE_SPEC, migrated_session_factory)

    async with migrated_session_factory() as session:
        claimed = await queue.claim(limit=10, session=session)
        assert len(claimed) == 1
        # A second connection must not see an uncommitted claim.
        assert await _count_unclaimed(migrated_session_factory) == 1
        await session.rollback()

    assert await _count_unclaimed(migrated_session_factory) == 1


async def test_completing_with_values_retains_the_row(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The `schematic_jobs` acknowledgement shape, through the shared helper."""
    async with migrated_session_factory.begin() as session:
        job_id = (
            await session.execute(
                text(
                    "INSERT INTO schematic_jobs (operation, params, input_keys, error_context) "
                    "VALUES ('capabilities', '{}'::jsonb, ARRAY[]::text[], '{}'::jsonb) RETURNING id"
                )
            )
        ).scalar_one()
    queue = ClaimedRowQueue(SCHEMATIC_JOB_SPEC, migrated_session_factory)

    (row,) = await queue.claim(limit=10)
    outcome = await queue.complete(
        (SCHEMATIC_JOB_SPEC.key[0] == job_id,),
        queue.token_of(row),
        values={"completed_at": text("now()")},
    )

    assert outcome.applied is True
    completed, claimed_at, token = await _row(
        migrated_session_factory,
        "SELECT completed_at IS NOT NULL, claimed_at, claim_token FROM schematic_jobs",
    )
    assert (completed, claimed_at, token) == (True, None, None)
    # Retained but no longer pending, so it is not handed out again.
    assert await queue.claim(limit=10) == ()


@pytest.mark.parametrize("attempts", range(13))
async def test_the_sql_and_python_backoff_agree(
    migrated_session_factory: async_sessionmaker[AsyncSession],
    attempts: int,
) -> None:
    """`fail` and `fail_batch` must back off identically, or a lease drifts from a row."""
    async with migrated_session_factory() as session:
        in_postgres = await session.scalar(select(retry_delay_sql(literal(attempts))))
    assert in_postgres == retry_delay(attempts)


async def test_batch_and_single_retries_use_each_rows_incremented_attempt_count(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    queue = ClaimedRowQueue(DISCORD_SYNC_QUEUE_SPEC)
    previous_attempts = (0, 1, 8, 10_000)
    async with migrated_session_factory.begin() as session:
        for attempts in previous_attempts:
            for kind in ("single", "batch"):
                await session.execute(
                    text(
                        "INSERT INTO discord_sync_queue (resource_kind, source_key, attempts) "
                        "VALUES ('build', :key, :attempts)"
                    ),
                    {"key": f"{kind}-{attempts}", "attempts": attempts},
                )
        claimed = await queue.claim(limit=8, session=session)
        batch_tokens = []
        for row in claimed:
            token = queue.token_of(row)
            if row.source_key.startswith("batch-"):
                batch_tokens.append(token)
            else:
                outcome = await queue.fail(
                    (DISCORD_SYNC_QUEUE_SPEC.key[0] == row.id,),
                    token,
                    attempts=row.attempts,
                    error="retry",
                    max_attempts=None,
                    session=session,
                )
                assert outcome.applied
        assert await queue.fail_batch(batch_tokens, error="retry", session=session) == len(previous_attempts)
        rows = (
            await session.execute(
                text(
                    "SELECT source_key, attempts, available_at - now() AS delay, claimed_at, claim_token, last_error "
                    "FROM discord_sync_queue"
                )
            )
        ).all()

    assert len(rows) == 8
    for source_key, attempts, delay, claimed_at, claim_token, last_error in rows:
        assert attempts == int(source_key.split("-")[1]) + 1
        assert delay == retry_delay(attempts)
        assert (claimed_at, claim_token, last_error) == (None, None, "retry")


async def _expire_the_claim(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Age a claim past the visibility timeout without waiting five minutes."""
    async with session_factory.begin() as session:
        await session.execute(text("UPDATE discord_sync_queue SET claimed_at = now() - interval '6 minutes'"))


async def _count_unclaimed(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return (
            await session.execute(text("SELECT count(*) FROM discord_sync_queue WHERE claim_token IS NULL"))
        ).scalar_one()


async def test_service_claim_is_not_published_when_commit_fails(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    class FailingUnitOfWork(SqlAlchemyUnitOfWork):
        async def commit(self) -> None:
            raise RuntimeError("claim commit failed")

    await _seed_sync_job(migrated_session_factory)
    service = DiscordReconciliationService(uow_factory=partial(FailingUnitOfWork, migrated_session_factory))
    with pytest.raises(RuntimeError, match="claim commit failed"):
        await service.claim()

    service = DiscordReconciliationService(uow_factory=partial(SqlAlchemyUnitOfWork, migrated_session_factory))
    claimed = await service.claim()
    assert len(claimed) == 1
    assert await service.complete(claimed[0])

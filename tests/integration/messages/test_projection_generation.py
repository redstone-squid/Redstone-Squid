"""Coverage for the generation counter that decides whether a Discord post is stale.

The desired-state projection is driven entirely by Postgres triggers, so its failure
modes only appear against a real database. Nothing else exercises them.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed(session: AsyncSession) -> int:
    """Create a guild, an account, and a build with one tracked message."""
    await session.execute(text("INSERT INTO server_settings (server_id) VALUES (999)"))
    account_id = (await session.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id"))).scalar_one()
    build_id = (
        await session.execute(
            text(
                "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
                "VALUES (0, 'Utility', :account_id, false) RETURNING id"
            ),
            {"account_id": account_id},
        )
    ).scalar_one()
    await session.execute(
        text(
            "INSERT INTO messages ("
            "id, server_id, channel_id, author_id, purpose, projection_resource_kind, projection_source_key"
            ") VALUES (100, 999, 200, 300, 'view_confirmed_build', 'build', :build_id)"
        ),
        {"build_id": str(build_id)},
    )
    return build_id


async def _generation(session: AsyncSession, build_id: int) -> int | None:
    return (
        await session.execute(
            text("SELECT generation FROM discord_sync_queue WHERE resource_kind = 'build' AND source_key = :key"),
            {"key": str(build_id)},
        )
    ).scalar_one_or_none()


async def _revisions(session: AsyncSession) -> tuple[int, int]:
    row = (await session.execute(text("SELECT desired_revision, applied_revision FROM messages WHERE id = 100"))).one()
    return (row[0], row[1])


@pytest.mark.xfail(
    reason="Completing a sync job deletes the queue row, so generation restarts at 1 and "
    "the desired revision is projected below applied_revision.",
    raises=IntegrityError,
    strict=True,
)
async def test_generation_survives_job_completion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build stays editable after a coalesced refresh has been rendered and acknowledged.

    Each step commits separately because `enqueue_discord_sync` stamps `now()`, which is
    the transaction timestamp: coalescing two edits inside one transaction would leave
    `enqueued_at` unchanged and never bump the generation.
    """
    async with migrated_session_factory.begin() as session:
        build_id = await _seed(session)

    # A second edit coalesces onto the queued row and bumps the generation past 1.
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET ai_generated = true WHERE id = :id"), {"id": build_id})

    async with migrated_session_factory() as session:
        assert await _generation(session, build_id) == 2
        assert await _revisions(session) == (2, 1)

    # The reconciler renders the card and acknowledges the generation it rendered.
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE messages SET applied_revision = 2 WHERE id = 100 AND desired_revision = 2"))

    async with migrated_session_factory() as session:
        assert await _revisions(session) == (2, 2)

    # `ClaimedRowQueue.complete` deletes the acknowledged row, taking the counter with it.
    async with migrated_session_factory.begin() as session:
        await session.execute(
            text("DELETE FROM discord_sync_queue WHERE resource_kind = 'build' AND source_key = :key"),
            {"key": str(build_id)},
        )

    # The next edit re-inserts at generation 1, projecting a desired revision below the
    # applied one. This must not take the user's build edit down with it.
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET ai_generated = false WHERE id = :id"), {"id": build_id})

    async with migrated_session_factory() as session:
        desired, applied = await _revisions(session)
        assert desired >= applied

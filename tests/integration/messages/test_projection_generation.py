"""Coverage for the generation counter that decides whether a Discord post is stale.

The generation is driven entirely by Postgres triggers, so its failure modes only
appear against a real database. Nothing else exercises them.
"""

from sqlalchemy import text
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


async def test_generation_survives_job_completion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build stays editable after a coalesced refresh has been rendered and acknowledged.

    With a per-row counter this aborted the third edit: acknowledging the job deleted
    the queue row, the next enqueue restarted at 1, and projecting that 1 onto a message
    already applied at 2 violated `messages_projection_revisions_valid` — inside the
    statement doing the edit.

    Each step commits separately because `enqueue_discord_sync` stamps `now()`, which is
    the transaction timestamp: coalescing two edits inside one transaction would leave
    `enqueued_at` unchanged and never re-enqueue.
    """
    async with migrated_session_factory.begin() as session:
        build_id = await _seed(session)

    # A second edit coalesces onto the queued row and takes a fresh generation.
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET ai_generated = true WHERE id = :id"), {"id": build_id})

    async with migrated_session_factory() as session:
        coalesced = await _generation(session, build_id)
        assert coalesced is not None
        desired, applied = await _revisions(session)
        assert desired == coalesced
        assert applied < desired

    # The reconciler renders the card and acknowledges the generation it rendered.
    async with migrated_session_factory.begin() as session:
        await session.execute(
            text(
                "UPDATE messages SET applied_revision = :generation WHERE id = 100 AND desired_revision = :generation"
            ),
            {"generation": coalesced},
        )

    # `ClaimedRowQueue.complete` deletes the acknowledged row, taking its generation with it.
    async with migrated_session_factory.begin() as session:
        await session.execute(
            text("DELETE FROM discord_sync_queue WHERE resource_kind = 'build' AND source_key = :key"),
            {"key": str(build_id)},
        )

    # The edit that used to abort.
    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET ai_generated = false WHERE id = :id"), {"id": build_id})

    async with migrated_session_factory() as session:
        reissued = await _generation(session, build_id)
        assert reissued is not None
        # The sequence keeps climbing across the delete, so the re-enqueued generation
        # outranks what was already applied and the message is seen as stale again.
        assert reissued > coalesced
        desired, applied = await _revisions(session)
        assert desired == reissued
        assert applied < desired


async def test_generations_are_unique_across_resources(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Generations come from one sequence, so no two enqueues collide.

    A per-resource counter made generation 1 mean different things for different
    builds, which is only safe while nothing compares them.
    """
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO server_settings (server_id) VALUES (999)"))
        account_id = (await session.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id"))).scalar_one()
        for _ in range(3):
            await session.execute(
                text(
                    "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
                    "VALUES (0, 'Utility', :account_id, false)"
                ),
                {"account_id": account_id},
            )

    async with migrated_session_factory() as session:
        generations = list(
            (
                await session.scalars(text("SELECT generation FROM discord_sync_queue WHERE resource_kind = 'build'"))
            ).all()
        )

    assert len(generations) == 3
    assert len(set(generations)) == 3

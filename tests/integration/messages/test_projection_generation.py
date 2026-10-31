"""Coverage for the generation counter that decides whether a Discord post is stale.

The counter is maintained entirely by a Postgres trigger drawing from a sequence, so
its failure modes only appear against a real database.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def _seed(session: AsyncSession) -> int:
    """Create a guild, an account, and a build with one post rendering it."""
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
        text("INSERT INTO messages (id, guild_id, channel_id, author_id) VALUES (100, 999, 200, 300)")
    )
    await session.execute(
        text(
            "INSERT INTO discord_posts ("
            "message_id, channel_id, resource_kind, resource_key, surface, applied_revision"
            ") VALUES (100, 200, 'build', :key, 'build_card', 0)"
        ),
        {"key": str(build_id)},
    )
    return build_id


async def _generation(session: AsyncSession, build_id: int) -> int | None:
    return (
        await session.execute(
            text("SELECT generation FROM discord_sync_queue WHERE resource_kind = 'build' AND source_key = :key"),
            {"key": str(build_id)},
        )
    ).scalar_one_or_none()


async def _applied(session: AsyncSession) -> int:
    return (
        await session.execute(text("SELECT applied_revision FROM discord_posts WHERE message_id = 100"))
    ).scalar_one()


async def test_generation_survives_job_completion(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A build stays editable after a coalesced refresh has been rendered and acknowledged.

    With a per-resource counter this aborted the third edit: acknowledging the job
    deleted the queue row, the next enqueue restarted at 1, and projecting that 1 onto a
    row already applied at a higher generation violated a check constraint — inside the
    statement doing the edit, so it took the user's write down with it.

    Each step commits separately because `enqueue_discord_sync` stamps `now()`, which is
    the transaction timestamp: coalescing two edits inside one transaction would leave
    `enqueued_at` unchanged and never re-enqueue.
    """
    async with migrated_session_factory.begin() as session:
        build_id = await _seed(session)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE builds SET ai_generated = true WHERE id = :id"), {"id": build_id})

    async with migrated_session_factory() as session:
        coalesced = await _generation(session, build_id)
        assert coalesced is not None
        assert await _applied(session) < coalesced

    # The reconciler renders the card and acknowledges the generation it rendered.
    async with migrated_session_factory.begin() as session:
        await session.execute(
            text("UPDATE discord_posts SET applied_revision = :generation WHERE message_id = 100"),
            {"generation": coalesced},
        )

    # `ClaimedRowQueue.complete` deletes the acknowledged row, taking its generation.
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
        # outranks what was already applied and the post is seen as stale again.
        assert reissued > coalesced
        assert await _applied(session) < reissued


async def test_generations_are_unique_across_resources(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Generations come from one sequence, so no two enqueues collide.

    A per-resource counter made generation 1 mean different things for different builds,
    which is only safe while nothing compares them.
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

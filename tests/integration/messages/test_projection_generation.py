"""PostgreSQL sequence generations identify Discord posts that need another render."""

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
    await session.execute(text("INSERT INTO utilities (build_id) VALUES (:id)"), {"id": build_id})
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
    assert isinstance(build_id, int)
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
    """A new queue row must have a generation newer than the revision already applied to a post.

    Separate transactions model an edit, a completed render, queue acknowledgement, and a later edit.
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

    # A later edit recreates the deleted queue row.
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
    async with migrated_session_factory.begin() as session:
        await session.execute(text("INSERT INTO server_settings (server_id) VALUES (999)"))
        account_id = (await session.execute(text("INSERT INTO accounts DEFAULT VALUES RETURNING id"))).scalar_one()
        for _ in range(3):
            build_id = (
                await session.execute(
                    text(
                        "INSERT INTO builds (submission_status, category, submitter_account_id, ai_generated) "
                        "VALUES (0, 'Utility', :account_id, false) RETURNING id"
                    ),
                    {"account_id": account_id},
                )
            ).scalar_one()
            await session.execute(text("INSERT INTO utilities (build_id) VALUES (:id)"), {"id": build_id})

    async with migrated_session_factory() as session:
        generations = list(
            (
                await session.scalars(text("SELECT generation FROM discord_sync_queue WHERE resource_kind = 'build'"))
            ).all()
        )

    assert len(generations) == 3
    assert len(set(generations)) == 3

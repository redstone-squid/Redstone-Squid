"""Claim fencing and backoff for the search projection queue.

These replace two unit tests that drove `SearchProjectionStore` with a mocked
session and asserted on mutated ORM attributes. Acknowledgement is now a fenced
`UPDATE`, so the assertions have to look at the row the database actually holds.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.search.infrastructure.projection import SearchProjectionStore


async def _enqueue(session: AsyncSession, source_key: str = "42", *, attempts: int = 0) -> None:
    await session.execute(
        text(
            "INSERT INTO search_projection_queue (resource_kind, source_key, action, attempts) "
            "VALUES ('build', :key, 'upsert', :attempts)"
        ),
        {"key": source_key, "attempts": attempts},
    )


async def test_a_failed_projection_backs_off_instead_of_rerunning_every_poll(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Opting out of the shared helper cost this queue its exponential backoff."""
    async with migrated_session_factory() as session, session.begin():
        await _enqueue(session)
        store = SearchProjectionStore(session)
        (item,) = await store.claim(limit=10)

        dead_lettered = await store.retry(item.id, store.token_of(item), item.attempts, RuntimeError("temporary"))

        assert dead_lettered is False

    async with migrated_session_factory() as session:
        attempts, locked_at, token, dead_at, backed_off, error = (
            await session.execute(
                text(
                    "SELECT attempts, locked_at, claim_token, dead_at, available_at > now(), last_error "
                    "FROM search_projection_queue"
                )
            )
        ).one()
    assert (attempts, locked_at, token, dead_at, backed_off, error) == (1, None, None, None, True, "temporary")


async def test_a_projection_at_the_attempt_limit_is_retained_as_a_dead_letter(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with migrated_session_factory() as session, session.begin():
        await _enqueue(session, attempts=4)
        store = SearchProjectionStore(session)
        (item,) = await store.claim(limit=10)

        dead_lettered = await store.retry(
            item.id,
            store.token_of(item),
            item.attempts,
            RuntimeError("projection failed"),
            max_attempts=5,
        )

        assert dead_lettered is True

    async with migrated_session_factory() as session:
        attempts, locked_at, dead, error = (
            await session.execute(
                text("SELECT attempts, locked_at, dead_at IS NOT NULL, last_error FROM search_projection_queue")
            )
        ).one()
    assert (attempts, locked_at, dead, error) == (5, None, True, "projection failed")


async def test_a_reclaimed_projection_cannot_be_completed_by_the_previous_holder(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """`complete` was a bare `session.delete(item)`, so both holders deleted the row."""
    async with migrated_session_factory() as session, session.begin():
        await _enqueue(session)
        stale_store = SearchProjectionStore(session)
        (stale,) = await stale_store.claim(limit=10)
        stale_token = stale_store.token_of(stale)

    async with migrated_session_factory.begin() as session:
        await session.execute(text("UPDATE search_projection_queue SET locked_at = now() - interval '6 minutes'"))

    async with migrated_session_factory() as session, session.begin():
        fresh_store = SearchProjectionStore(session)
        (fresh,) = await fresh_store.claim(limit=10)
        assert fresh_store.token_of(fresh) != stale_token

        assert await fresh_store.complete(stale.id, stale_token) is False

    async with migrated_session_factory() as session:
        surviving = (await session.execute(text("SELECT count(*) FROM search_projection_queue"))).scalar_one()
    assert surviving == 1


async def test_a_caller_owned_projection_claim_is_not_committed(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The claim joins the projector's unit of work rather than ending it."""
    async with migrated_session_factory.begin() as session:
        await _enqueue(session)

    async with migrated_session_factory() as session:
        assert len(await SearchProjectionStore(session).claim(limit=10)) == 1
        await session.rollback()

    async with migrated_session_factory() as session:
        unclaimed = (
            await session.execute(text("SELECT count(*) FROM search_projection_queue WHERE claim_token IS NULL"))
        ).scalar_one()
    assert unclaimed == 1

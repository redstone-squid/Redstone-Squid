"""Database enforcement serializes concurrent role inclusion graph edits."""

import anyio
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def test_concurrent_includes_cannot_commit_a_cycle(
    migrated_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    sessions = migrated_session_factory
    async with sessions.begin() as session:
        roles = tuple(
            (
                await session.execute(
                    text(
                        "INSERT INTO permission_roles (slug, name) VALUES ('cycle-aa', 'A'), ('cycle-bb', 'B') RETURNING id"
                    )
                )
            ).scalars()
        )
    started = anyio.Event()

    async def reverse_edge() -> None:
        async with sessions() as session:
            started.set()
            with pytest.raises(IntegrityError, match="inclusion cycle"):
                await session.execute(
                    text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:a, :b)"),
                    {"a": roles[1], "b": roles[0]},
                )
            await session.rollback()

    async with sessions() as first:
        await first.execute(
            text("INSERT INTO permission_role_includes (role_id, included_role_id) VALUES (:a, :b)"),
            {"a": roles[0], "b": roles[1]},
        )
        with anyio.fail_after(10):
            async with anyio.create_task_group() as group:
                group.start_soon(reverse_edge)
                await started.wait()
                # Wait until PostgreSQL reports the competing transaction blocked on our graph lock.
                async with sessions() as observer:
                    while not await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                            "AND classid = 1936812391 AND objid = 1 AND NOT granted)"
                        )
                    ):
                        await anyio.sleep(0.01)
                await first.commit()
    async with sessions() as session:
        assert (
            await session.scalar(
                text("SELECT count(*) FROM permission_role_includes WHERE role_id IN (:a, :b)"),
                {"a": roles[0], "b": roles[1]},
            )
            == 1
        )

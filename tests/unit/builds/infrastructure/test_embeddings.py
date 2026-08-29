"""Build embedding adapter validation tests."""

from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.infrastructure.embeddings import PostgresBuildIndex


class _UnusedSessionFactory:
    def begin(self) -> None:
        raise AssertionError("invalid vectors must be rejected before opening a database session")


async def test_postgres_index_rejects_a_wrong_vector_dimension() -> None:
    sessions = cast(async_sessionmaker[AsyncSession], _UnusedSessionFactory())
    index = PostgresBuildIndex(sessions)

    with pytest.raises(ValueError, match="exactly 1536"):
        await index.upsert(1, [1.0, 2.0, 3.0])

    assert await index.find_nearest([1.0, 2.0, 3.0]) is None

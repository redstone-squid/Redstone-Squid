"""Build embedding adapter validation tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from squid.builds.infrastructure.embeddings import PostgresBuildIndex


def unused_session() -> AsyncSession:
    raise AssertionError("Invalid vectors must not execute SQL.")


async def test_postgres_index_rejects_a_wrong_vector_dimension() -> None:
    index = PostgresBuildIndex(unused_session)

    with pytest.raises(ValueError, match="exactly 1536"):
        await index.upsert(1, [1.0, 2.0, 3.0])

    assert await index.find_nearest([1.0, 2.0, 3.0]) is None

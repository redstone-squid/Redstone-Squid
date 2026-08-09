"""Build embedding adapter validation tests."""

from unittest.mock import MagicMock

import pytest

from squid.builds.infrastructure.embeddings import PostgresBuildIndex


async def test_postgres_index_rejects_a_wrong_vector_dimension() -> None:
    index = PostgresBuildIndex(MagicMock())

    with pytest.raises(ValueError, match="exactly 1536"):
        await index.upsert(1, [1.0, 2.0, 3.0])

    assert await index.find_nearest([1.0, 2.0, 3.0]) is None

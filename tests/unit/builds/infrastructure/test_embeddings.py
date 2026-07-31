"""Build embedding adapter tests."""

from squid.builds.infrastructure.embeddings import VecsBuildIndex


async def test_vecs_index_is_disabled_without_connection() -> None:
    index = VecsBuildIndex(None)

    await index.upsert(1, [1.0, 2.0, 3.0])

    assert await index.find_nearest([1.0, 2.0, 3.0]) is None

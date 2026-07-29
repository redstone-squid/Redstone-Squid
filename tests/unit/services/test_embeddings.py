"""Tests for build embedding application coordination."""

from squid.builds.application import BuildEmbeddingService
from squid.builds.domain import Build


class FakeEmbeddingModel:
    def __init__(self, embedding: list[float] | None) -> None:
        self.embedding = embedding
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float] | None:
        self.inputs.append(text)
        return self.embedding


class FakeVectorIndex:
    def __init__(self) -> None:
        self.upserts: list[tuple[int, list[float]]] = []
        self.nearest: int | None = 42

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        self.upserts.append((build_id, embedding))

    async def find_nearest(self, embedding: list[float]) -> int | None:
        return self.nearest


async def test_prepare_index_and_search_share_embedding_abstractions() -> None:
    model = FakeEmbeddingModel([1.0, 2.0])
    index = FakeVectorIndex()
    service = BuildEmbeddingService(model, index)
    build = Build(id=7)

    await service.prepare(build)
    await service.index(build)
    result = await service.find_build_id("compact door")

    assert build.embedding == [1.0, 2.0]
    assert index.upserts == [(7, [1.0, 2.0])]
    assert result == 42
    assert model.inputs[-1] == "compact door"


async def test_missing_embedding_skips_index_and_search() -> None:
    index = FakeVectorIndex()
    service = BuildEmbeddingService(FakeEmbeddingModel(None), index)
    build = Build(id=7)

    await service.prepare(build)
    await service.index(build)

    assert await service.find_build_id("query") is None
    assert index.upserts == []

"""Application coordination for build embeddings and semantic lookup."""

from typing import Protocol

from squid.builds.domain import Build


class EmbeddingModel(Protocol):
    """Generate an embedding vector for text."""

    async def embed(self, text: str) -> list[float] | None:
        """The vector for `text`, or None when the provider is unconfigured or fails."""
        ...


class BuildVectorIndex(Protocol):
    """Persist and query build embedding vectors."""

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        """Store a build's vector, replacing any previous one.

        Raises:
            ValueError: If the vector's length is not the configured embedding dimension.
        """
        ...

    async def find_nearest(self, embedding: list[float]) -> int | None:
        """The ID of the closest indexed build by cosine distance, or None when nothing matches."""
        ...


class BuildEmbeddingService:
    """Generate, attach, index, and query build embeddings."""

    def __init__(self, model: EmbeddingModel, index: BuildVectorIndex) -> None:
        self._model = model
        self._index = index

    async def prepare(self, build: Build) -> None:
        """Generate and attach an embedding before relational persistence."""
        build.embedding = await self._model.embed(str(build))

    async def index(self, build: Build) -> None:
        """Index an already-persisted build when it has an embedding."""
        if build.id is not None and build.embedding is not None:
            await self._index.upsert(build.id, build.embedding)

    async def find_build_id(self, query: str) -> int | None:
        """Find the build nearest to a natural-language query, or None when the query cannot be embedded."""
        embedding = await self._model.embed(query)
        if embedding is None:
            return None
        return await self._index.find_nearest(embedding)

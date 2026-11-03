"""Application coordination for build embeddings and semantic lookup."""

from typing import Protocol

from squid.builds.models import Build
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory


class EmbeddingModel(Protocol):
    """Generate an embedding vector for text."""

    async def embed(self, text: str) -> list[float] | None: ...


class BuildVectorIndex(Protocol):
    """Persist and query build embedding vectors."""

    async def upsert(self, build_id: int, embedding: list[float]) -> None: ...

    async def find_nearest(self, embedding: list[float]) -> int | None: ...


class BuildEmbeddingService:
    """Generate, attach, index, and query build embeddings."""

    def __init__(self, model: EmbeddingModel, *, uow_factory: UnitOfWorkFactory[BuildVectorUnitOfWork]) -> None:
        self._model = model
        self._uow_factory = uow_factory

    async def prepare(self, build: Build) -> None:
        """Generate and attach an embedding before relational persistence."""
        build.embedding = await self._model.embed(str(build))

    async def index(self, build: Build) -> None:
        """Index an already-persisted build when it has an embedding."""
        if build.id is not None and build.embedding is not None:
            async with self._uow_factory() as uow:
                await uow.build_vectors.upsert(build.id, build.embedding)
                await uow.commit()

    async def find_build_id(self, query: str) -> int | None:
        """Find the build nearest to a natural-language query."""
        embedding = await self._model.embed(query)
        if embedding is None:
            return None
        async with self._uow_factory() as uow:
            return await uow.build_vectors.find_nearest(embedding)


class BuildVectorUnitOfWork(UnitOfWork, Protocol):
    @property
    def build_vectors(self) -> BuildVectorIndex: ...

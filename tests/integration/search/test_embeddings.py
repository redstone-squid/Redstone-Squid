"""Integration coverage for pgvector embedding ownership and fencing."""

from collections.abc import AsyncGenerator

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.search.application import SearchEmbeddingService
from squid.search.infrastructure.embeddings import PostgresSearchEmbeddingQueue, PostgresSemanticCandidateProvider
from squid.search.infrastructure.models import SearchDocument, SearchEmbeddingQueueItem

pytestmark = pytest.mark.asyncio

_TABLES = [Base.metadata.tables["search_documents"], Base.metadata.tables["search_embedding_queue"]]


class FakeEmbeddingModel:
    def __init__(self, embedding: list[float] | None, model_name: str = "test-model") -> None:
        self.embedding = embedding
        self.model_name = model_name
        self.inputs: list[str] = []

    async def embed(self, text: str) -> list[float] | None:
        self.inputs.append(text)
        return self.embedding


@pytest.fixture
async def search_embedding_tables(async_engine: AsyncEngine) -> AsyncGenerator[None, None]:
    async with async_engine.begin() as connection:
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await connection.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        await connection.run_sync(Base.metadata.create_all, tables=_TABLES)
    try:
        yield
    finally:
        async with async_engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all, tables=list(reversed(_TABLES)))


async def _document(session_factory: async_sessionmaker[AsyncSession], source_key: str, *, source_hash: str) -> int:
    async with session_factory.begin() as session:
        document = SearchDocument(
            resource_kind="build",
            source_key=source_key,
            title=f"Build {source_key}",
            normalized_title=f"build {source_key}",
            fuzzy_text=f"build {source_key}",
            source_hash=source_hash,
        )
        session.add(document)
        await session.flush()
        return document.id


async def test_worker_embeds_the_exact_claimed_source_revision(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del search_embedding_tables
    document_id = await _document(async_session_factory, "1", source_hash="v1")
    async with async_session_factory.begin() as session:
        session.add(SearchEmbeddingQueueItem(document_id=document_id, source_hash="v1"))
    model = FakeEmbeddingModel([1.0] + [0.0] * 1535)
    service = SearchEmbeddingService(model, PostgresSearchEmbeddingQueue(async_session_factory))

    assert await service.process_batch() == (1, 0)

    async with async_session_factory() as session:
        document = await session.get(SearchDocument, document_id)
        queue_count = await session.scalar(select(func.count()).select_from(SearchEmbeddingQueueItem))
    assert document is not None
    assert document.embedding is not None
    assert list(document.embedding) == model.embedding
    assert document.embedding_model == "test-model"
    assert queue_count == 0
    assert model.inputs == ["Build 1"]


async def test_source_changes_fence_a_stale_embedding_completion(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del search_embedding_tables
    document_id = await _document(async_session_factory, "2", source_hash="v1")
    async with async_session_factory.begin() as session:
        session.add(SearchEmbeddingQueueItem(document_id=document_id, source_hash="v1"))
    queue = PostgresSearchEmbeddingQueue(async_session_factory)
    stale = (await queue.claim(limit=1))[0]
    async with async_session_factory.begin() as session:
        await session.execute(update(SearchDocument).where(SearchDocument.id == document_id).values(source_hash="v2"))
        await session.execute(
            update(SearchEmbeddingQueueItem)
            .where(SearchEmbeddingQueueItem.document_id == document_id)
            .values(source_hash="v2", locked_at=None)
        )

    assert await queue.complete(stale, [1.0] + [0.0] * 1535, "test-model") is False
    async with async_session_factory() as session:
        document = await session.get(SearchDocument, document_id)
    assert document is not None
    assert document.embedding is None


async def test_exhausted_embedding_work_is_retained_as_a_dead_letter(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del search_embedding_tables
    document_id = await _document(async_session_factory, "3", source_hash="v1")
    async with async_session_factory.begin() as session:
        session.add(SearchEmbeddingQueueItem(document_id=document_id, source_hash="v1"))
    service = SearchEmbeddingService(
        FakeEmbeddingModel(None),
        PostgresSearchEmbeddingQueue(async_session_factory),
        max_attempts=1,
    )

    assert await service.process_batch() == (0, 1)

    async with async_session_factory() as session:
        queue_item = await session.get(SearchEmbeddingQueueItem, document_id)
    assert queue_item is not None
    assert queue_item.dead_at is not None
    assert queue_item.last_error is not None


async def test_semantic_candidates_are_ranked_by_postgres_cosine_distance(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del search_embedding_tables
    first = await _document(async_session_factory, "near", source_hash="v1")
    second = await _document(async_session_factory, "far", source_hash="v1")
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == first)
            .values(embedding=[1.0] + [0.0] * 1535, embedding_model="test-model")
        )
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == second)
            .values(embedding=[0.0, 1.0] + [0.0] * 1534, embedding_model="test-model")
        )
    provider = PostgresSemanticCandidateProvider(
        async_session_factory,
        FakeEmbeddingModel([1.0] + [0.0] * 1535),
    )

    candidates = await provider.candidates("nearest", limit=2)

    assert [candidate.source_key for candidate in candidates] == ["near", "far"]

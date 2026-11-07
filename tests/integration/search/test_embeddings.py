"""Embedding jobs and semantic search against PostgreSQL with pgvector.

Workers may save an embedding only for the source content they claimed. Search must
filter those vectors by model and visibility, with lexical results available if vector
comparison fails.
"""

from collections.abc import AsyncGenerator
from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from squid.persistence.base import Base
from squid.persistence.unit_of_work import SqlAlchemyUnitOfWork
from squid.search.application import SearchEmbeddingService, SearchQueryParser
from squid.search.application.ports import QueryEmbedding
from squid.search.domain import SearchMode, SearchRequest, SearchScope
from squid.search.models import SearchDocument, SearchEmbeddingQueueItem
from tests.support.unit_of_work import transact

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
async def search_embedding_tables(async_engine: AsyncEngine) -> AsyncGenerator[None]:
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
    service = SearchEmbeddingService(model, uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory))

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
    queue = partial(SqlAlchemyUnitOfWork, async_session_factory)
    stale = (await transact(queue, lambda uow: uow.search_embeddings.claim(limit=1), commit=True))[0]
    async with async_session_factory.begin() as session:
        await session.execute(update(SearchDocument).where(SearchDocument.id == document_id).values(source_hash="v2"))
        await session.execute(
            update(SearchEmbeddingQueueItem)
            .where(SearchEmbeddingQueueItem.document_id == document_id)
            .values(source_hash="v2", claimed_at=None)
        )

    assert (
        await transact(
            queue, lambda uow: uow.search_embeddings.complete(stale, [1.0] + [0.0] * 1535, "test-model"), commit=True
        )
        is False
    )
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
        FakeEmbeddingModel(None), uow_factory=partial(SqlAlchemyUnitOfWork, async_session_factory), max_attempts=1
    )

    assert await service.process_batch() == (0, 1)

    async with async_session_factory() as session:
        queue_item = await session.get(SearchEmbeddingQueueItem, document_id)
    assert queue_item is not None
    assert queue_item.dead_at is not None
    assert queue_item.last_error is not None


async def test_semantic_search_ranks_vectors_and_respects_visibility_and_query_filters(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    del search_embedding_tables
    first = await _document(async_session_factory, "near", source_hash="v1")
    second = await _document(async_session_factory, "far", source_hash="v1")
    hidden = await _document(async_session_factory, "hidden", source_hash="v1")
    outside = await _document(async_session_factory, "outside", source_hash="v1")
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == first)
            .values(embedding=[1.0] + [0.0] * 1535, embedding_model="test-model", status="confirmed")
        )
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == second)
            .values(embedding=[0.0, 1.0] + [0.0] * 1534, embedding_model="test-model", status="confirmed")
        )
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == hidden)
            .values(embedding=[1.0] + [0.0] * 1535, embedding_model="test-model", status="pending")
        )
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == outside)
            .values(
                embedding=[1.0] + [0.0] * 1535,
                embedding_model="test-model",
                status="confirmed",
                title="Unrelated",
                normalized_title="unrelated",
                fuzzy_text="unrelated",
            )
        )
    request = SearchRequest("title:build", mode=SearchMode.SEMANTIC, scope=SearchScope.BUILDS)
    parsed = SearchQueryParser().parse(request.query)
    embedding = QueryEmbedding([1.0] + [0.0] * 1535, "test-model")
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        page = await uow.search.search(request, parsed, offset=0, query_embedding=embedding)
        wrong_model = await uow.search.search(
            request, parsed, offset=0, query_embedding=replace(embedding, model="other-model")
        )
        lexical = await uow.search.search(replace(request, mode=SearchMode.LEXICAL), parsed, offset=0)

    assert [hit.source_id for hit in page.hits] == ["near", "far"]
    assert not page.warnings
    assert wrong_model.hits == lexical.hits
    assert not wrong_model.warnings


async def test_failed_vector_lookup_preserves_lexical_results_and_the_read_scope(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    identifier = await _document(async_session_factory, "1", source_hash="v1")
    async with async_session_factory.begin() as session:
        await session.execute(
            update(SearchDocument)
            .where(SearchDocument.id == identifier)
            .values(status="confirmed", embedding=[1.0] + [0.0] * 1535, embedding_model="test-model")
        )
    request = SearchRequest("build", mode=SearchMode.SEMANTIC, scope=SearchScope.BUILDS)
    parsed = SearchQueryParser().parse(request.query)
    async with SqlAlchemyUnitOfWork(async_session_factory) as uow:
        # PostgreSQL rejects comparing vectors with different dimensions.
        failed = await uow.search.search(
            request, parsed, offset=0, query_embedding=QueryEmbedding([1.0, 0.0], "test-model")
        )
        unavailable = await uow.search.search(request, parsed, offset=0)
        lexical = await uow.search.search(replace(request, mode=SearchMode.LEXICAL), parsed, offset=0)

    assert [hit.source_id for hit in failed.hits] == ["1"]
    assert failed.hits == unavailable.hits == lexical.hits
    assert failed.warnings == unavailable.warnings
    assert failed.warnings
    assert not lexical.warnings


async def test_stale_completion_preserves_other_writes_in_the_unit_of_work(
    search_embedding_tables: None,
    async_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    first = await _document(async_session_factory, "first", source_hash="v1")
    second = await _document(async_session_factory, "second", source_hash="v1")
    async with async_session_factory.begin() as session:
        session.add_all(
            SearchEmbeddingQueueItem(document_id=identifier, source_hash="v1") for identifier in (first, second)
        )
    factory = partial(SqlAlchemyUnitOfWork, async_session_factory)
    claims = await transact(factory, lambda uow: uow.search_embeddings.claim(limit=2), commit=True)
    by_id = {job.document_id: job for job in claims}
    vector = [1.0] + [0.0] * 1535
    async with factory() as uow:
        assert await uow.search_embeddings.complete(by_id[first], vector, "test-model")
        assert not await uow.search_embeddings.complete(
            replace(by_id[second], claim_token=uuid4()), vector, "test-model"
        )
        await uow.commit()
    async with async_session_factory() as session:
        saved = await session.get(SearchDocument, first)
        stale = await session.get(SearchDocument, second)
        assert saved is not None
        assert saved.embedding is not None
        assert stale is not None
        assert stale.embedding is None
        assert await session.get(SearchEmbeddingQueueItem, first) is None
        assert await session.get(SearchEmbeddingQueueItem, second) is not None

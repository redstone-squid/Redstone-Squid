"""PostgreSQL embedding queue and semantic candidate adapters."""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import delete, func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue
from squid.search.application.embeddings import SearchEmbeddingJob, SearchEmbeddingModel
from squid.search.infrastructure.models import SearchDocument, SearchEmbeddingQueueItem
from squid.search.infrastructure.repository import SemanticCandidate


class PostgresSearchEmbeddingQueue:
    """Claim changed search documents and fence vector writes by source hash."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(
            session_factory,
            SearchEmbeddingQueueItem,
            ready_at=SearchEmbeddingQueueItem.enqueued_at,
            claimed_at=SearchEmbeddingQueueItem.locked_at,
            dead_at=SearchEmbeddingQueueItem.dead_at,
        )

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]:
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    select(SearchEmbeddingQueueItem, SearchDocument)
                    .join(SearchDocument, SearchDocument.id == SearchEmbeddingQueueItem.document_id)
                    .where(
                        SearchEmbeddingQueueItem.enqueued_at <= func.now(),
                        self._queue.reclaimable(),
                    )
                    .order_by(SearchEmbeddingQueueItem.enqueued_at, SearchEmbeddingQueueItem.document_id)
                    .limit(limit)
                    .with_for_update(skip_locked=True, of=SearchEmbeddingQueueItem)
                )
            ).all()
            queue_rows = tuple(row[0] for row in rows)
            claimed_at = await self._queue.stamp(queue_rows, session)
        return tuple(
            SearchEmbeddingJob(
                document_id=queue_row.document_id,
                source_hash=queue_row.source_hash,
                text=_embedding_text(document),
                attempts=queue_row.attempts,
                claimed_at=claimed_at,
            )
            for queue_row, document in rows
        )

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool:
        async with self._session_factory.begin() as session:
            owned = await session.scalar(
                select(SearchEmbeddingQueueItem.document_id)
                .where(
                    SearchEmbeddingQueueItem.document_id == job.document_id,
                    SearchEmbeddingQueueItem.source_hash == job.source_hash,
                    SearchEmbeddingQueueItem.locked_at == job.claimed_at,
                )
                .with_for_update()
            )
            if owned is None:
                return False
            updated = cast(
                CursorResult[Any],
                await session.execute(
                    update(SearchDocument)
                    .where(SearchDocument.id == job.document_id, SearchDocument.source_hash == job.source_hash)
                    .values(embedding=embedding, embedding_model=model)
                ),
            )
            if not updated.rowcount:
                return False
            await session.execute(
                delete(SearchEmbeddingQueueItem).where(
                    SearchEmbeddingQueueItem.document_id == job.document_id,
                    SearchEmbeddingQueueItem.source_hash == job.source_hash,
                    SearchEmbeddingQueueItem.locked_at == job.claimed_at,
                )
            )
        return True

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool:
        return await self._queue.fail(
            (
                SearchEmbeddingQueueItem.document_id == job.document_id,
                SearchEmbeddingQueueItem.source_hash == job.source_hash,
            ),
            job.claimed_at,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )


class PostgresSemanticCandidateProvider:
    """Generate a query vector and rank search documents with pgvector cosine distance."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        model: SearchEmbeddingModel,
    ) -> None:
        self._session_factory = session_factory
        self._model = model

    async def candidates(self, query: str, *, limit: int) -> Sequence[SemanticCandidate]:
        embedding = await self._model.embed(query)
        if embedding is None:
            return ()
        statement = (
            select(SearchDocument.resource_kind, SearchDocument.source_key)
            .where(
                SearchDocument.embedding.is_not(None),
                SearchDocument.embedding_model == self._model.model_name,
            )
            .order_by(SearchDocument.embedding.cosine_distance(embedding))
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(statement)).all()
        return tuple(SemanticCandidate(row.resource_kind, row.source_key) for row in rows)


def _embedding_text(document: SearchDocument) -> str:
    return "\n".join(
        part
        for part in (
            document.title,
            document.subtitle,
            document.description,
            " ".join(document.tags),
        )
        if part
    )

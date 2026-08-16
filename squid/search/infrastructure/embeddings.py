"""PostgreSQL embedding queue and semantic candidate adapters."""

from collections.abc import Sequence
from typing import Any, cast

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.search.application.embeddings import SearchEmbeddingJob, SearchEmbeddingModel
from squid.search.infrastructure.models import SearchDocument, SearchEmbeddingQueueItem
from squid.search.infrastructure.repository import SemanticCandidate

SEARCH_EMBEDDING_QUEUE_SPEC = QueueSpec(
    name="search_embeddings",
    model=SearchEmbeddingQueueItem,
    key=(SearchEmbeddingQueueItem.document_id,),
    available_at=SearchEmbeddingQueueItem.available_at,
    enqueued_at=SearchEmbeddingQueueItem.enqueued_at,
    claimed_at=SearchEmbeddingQueueItem.locked_at,
    claim_token=SearchEmbeddingQueueItem.claim_token,
    attempts=SearchEmbeddingQueueItem.attempts,
    last_error=SearchEmbeddingQueueItem.last_error,
    dead_at=SearchEmbeddingQueueItem.dead_at,
)


class PostgresSearchEmbeddingQueue:
    """Claim changed search documents and fence vector writes by source hash."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._queue = ClaimedRowQueue(SEARCH_EMBEDDING_QUEUE_SPEC, session_factory)

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]:
        rows = await self._queue.claim(limit=limit)
        if not rows:
            return ()
        # The document text is not on the queue row, so it is read after the claim
        # rather than joined into it. A document cannot change underneath the job:
        # `source_hash` is part of the fence, and any edit re-enqueues a new hash.
        async with self._session_factory() as session:
            documents = {
                document.id: document
                for document in (
                    await session.scalars(
                        select(SearchDocument).where(SearchDocument.id.in_([row.document_id for row in rows]))
                    )
                ).all()
            }
        return tuple(
            SearchEmbeddingJob(
                document_id=row.document_id,
                source_hash=row.source_hash,
                text=_embedding_text(document),
                attempts=row.attempts,
                claim_token=self._queue.token_of(row),
            )
            for row in rows
            if (document := documents.get(row.document_id)) is not None
        )

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool:
        async with self._session_factory.begin() as session:
            # The vector write and the acknowledgement have to agree, so both run
            # under this transaction and the document write gates the delete.
            updated = cast(
                CursorResult[Any],
                await session.execute(
                    update(SearchDocument)
                    .where(SearchDocument.id == job.document_id, SearchDocument.source_hash == job.source_hash)
                    .values(embedding=embedding, embedding_model=model)
                ),
            )
            if not updated.rowcount:
                await session.rollback()
                return False
            outcome = await self._queue.complete(self._identity(job), job.claim_token, session=session)
            if not outcome.applied:
                await session.rollback()
            return outcome.applied

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool:
        outcome = await self._queue.fail(
            self._identity(job),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
        )
        return outcome.dead_lettered

    @staticmethod
    def _identity(job: SearchEmbeddingJob) -> tuple[ColumnElement[bool], ...]:
        return (
            SearchEmbeddingQueueItem.document_id == job.document_id,
            SearchEmbeddingQueueItem.source_hash == job.source_hash,
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

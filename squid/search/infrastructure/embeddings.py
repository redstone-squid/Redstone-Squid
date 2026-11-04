"""PostgreSQL queue for search-document embedding jobs."""

from collections.abc import Callable, Sequence

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from squid.persistence.queue import ClaimedRowQueue, QueueSpec
from squid.search.application.embeddings import SearchEmbeddingJob
from squid.search.models import SearchDocument, SearchEmbeddingQueueItem

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
    """Claim documents for embedding and reject results from stale jobs.

    Completion checks the source hash so a vector for old text cannot replace
    one for the current document. The claim token separately prevents a worker
    from completing a job another worker has reclaimed. SearchEmbeddingService
    commits these queue and document updates in its unit of work.
    """

    def __init__(self, session: Callable[[], AsyncSession]) -> None:
        self._session = session
        self._queue = ClaimedRowQueue(SEARCH_EMBEDDING_QUEUE_SPEC)

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]:
        rows = await self._queue.claim(limit=limit, session=self._session())
        if not rows:
            return ()
        # Read text from the document after claiming its queue row. If the document
        # changes while embedding runs, complete() rejects the old source hash.
        session = self._session()
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
        """Save the vector and remove the job; return False if its claim or source is stale."""
        session = self._session()
        # Projection updates lock documents before queue rows. Keep that order.
        document = await session.scalar(
            select(SearchDocument)
            .where(SearchDocument.id == job.document_id, SearchDocument.source_hash == job.source_hash)
            .with_for_update()
        )
        if document is None:
            return False
        outcome = await self._queue.complete(self._identity(job), job.claim_token, session=session)
        if not outcome.applied:
            return False
        document.embedding = embedding
        document.embedding_model = model
        return True

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool:
        """Record a failed attempt and return whether the job became a dead letter."""
        outcome = await self._queue.fail(
            self._identity(job),
            job.claim_token,
            attempts=job.attempts,
            error=error,
            max_attempts=max_attempts,
            session=self._session(),
        )
        return outcome.dead_lettered

    @staticmethod
    def _identity(job: SearchEmbeddingJob) -> tuple[ColumnElement[bool], ...]:
        return (
            SearchEmbeddingQueueItem.document_id == job.document_id,
            SearchEmbeddingQueueItem.source_hash == job.source_hash,
        )


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

"""Durable search-embedding application coordination."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import anyio
from whenever import Instant

EMBEDDING_CALL_TIMEOUT_SECONDS = 300.0
"""Backstop for one embedding call, sized above the OpenAI adapter's own budget.

Deliberately looser than OPENAI_REQUEST_TIMEOUT_SECONDS and its retries, so a
well-behaved adapter reports its own failure rather than being pre-empted here.
"""


class SearchEmbeddingModel(Protocol):
    """Generate versioned vectors for searchable text."""

    @property
    def model_name(self) -> str: ...

    async def embed(self, text: str) -> list[float] | None: ...


@dataclass(frozen=True, slots=True)
class SearchEmbeddingJob:
    """One search document leased for embedding."""

    document_id: int
    source_hash: str
    text: str
    attempts: int
    claimed_at: Instant


class SearchEmbeddingQueue(Protocol):
    """Claim-fenced persistence for embedding work."""

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]: ...

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool: ...

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool: ...


class SearchEmbeddingService:
    """Drain changed search documents into pgvector."""

    def __init__(self, model: SearchEmbeddingModel, queue: SearchEmbeddingQueue, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            msg = "Embedding max_attempts must be positive."
            raise ValueError(msg)
        self._model = model
        self._queue = queue
        self._max_attempts = max_attempts

    async def process_batch(self, *, limit: int = 8) -> tuple[int, int]:
        """Embed a bounded batch, retaining exhausted documents as dead letters."""
        if not 1 <= limit <= 32:
            msg = "Embedding claim limit must be between 1 and 32."
            raise ValueError(msg)
        succeeded = failed = 0
        for job in await self._queue.claim(limit=limit):
            try:
                # The model port is a Protocol, so the adapter's own timeout is not
                # something this loop can rely on. Without a bound here a hung
                # provider stalls the periodic job that awaits process_batch to
                # completion, staling its heartbeat and failing worker readiness.
                # TimeoutError lands in the handler below and retries the job.
                with anyio.fail_after(EMBEDDING_CALL_TIMEOUT_SECONDS):
                    embedding = await self._model.embed(job.text)
                embedding = _require_embedding(embedding)
                if await self._queue.complete(job, embedding, self._model.model_name):
                    succeeded += 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self._queue.fail(job, str(error), max_attempts=self._max_attempts)
                failed += 1
        return succeeded, failed


def _require_embedding(embedding: list[float] | None) -> list[float]:
    if embedding is None:
        msg = "The configured embedding provider did not return a vector."
        raise RuntimeError(msg)
    return embedding

"""Durable search-embedding application coordination."""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import anyio

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr

EMBEDDING_CALL_TIMEOUT_SECONDS = 300.0
"""Backstop for one embedding call, looser than OPENAI_REQUEST_TIMEOUT_SECONDS and its retries.

A well-behaved adapter therefore reports its own failure rather than being pre-empted here.
"""


class SearchEmbeddingModel(Protocol):
    """Generate versioned vectors for searchable text."""

    @property
    def model_name(self) -> str:
        """The name stored beside every vector, so a query only matches vectors from the same model."""
        ...

    async def embed(self, text: str) -> list[float] | None:
        """Return the vector for `text`, or None when the provider is unconfigured or fails."""
        ...


@dataclass(frozen=True, slots=True)
class SearchEmbeddingJob:
    """One search document leased for embedding."""

    document_id: int
    source_hash: str
    text: str
    attempts: int
    claim_token: uuid.UUID
    """The database-minted fence this worker's acknowledgement must still match."""


class SearchEmbeddingQueue(Protocol):
    """Claim-fenced persistence for embedding work."""

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]:
        """Lease at most `limit` documents; each lease ends at `complete`, `fail`, or its timeout."""
        ...

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool:
        """Store the vector and acknowledge the job, returning whether the fence still held."""
        ...

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool:
        """Release the job for retry, returning whether this failure dead-lettered it instead."""
        ...


class SearchEmbeddingService:
    """Drain changed search documents into pgvector.

    Raises `InvalidStateError` when constructed with a non-positive `max_attempts`.
    """

    def __init__(self, model: SearchEmbeddingModel, queue: SearchEmbeddingQueue, *, max_attempts: int = 5) -> None:
        if max_attempts < 1:
            msg = tr(t"Embedding max_attempts must be positive.")
            raise InvalidStateError(msg)
        self._model = model
        self._queue = queue
        self._max_attempts = max_attempts

    async def process_batch(self, *, limit: int = 8) -> tuple[int, int]:
        """Embed at most `limit` claimed documents and return the succeeded and failed counts.

        A job that raises is released for retry, and dead-lettered once it has used `max_attempts`.

        Raises:
            InvalidStateError: If `limit` is outside 1-32.
        """
        if not 1 <= limit <= 32:
            msg = tr(t"Embedding claim limit must be between 1 and 32.")
            raise InvalidStateError(msg)
        succeeded = failed = 0
        for job in await self._queue.claim(limit=limit):
            try:
                # The model port is a Protocol, so no adapter timeout can be relied on here. A hung
                # provider would stall the periodic job awaiting process_batch, stale its heartbeat
                # and fail worker readiness. TimeoutError lands in the handler below and retries.
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
        msg = tr(t"The configured embedding provider did not return a vector.")
        raise InvalidStateError(msg)
    return embedding

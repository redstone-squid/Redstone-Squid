"""Durable search-embedding application coordination."""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import anyio

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.search.application.ports import SearchVectorRepository, SemanticCandidate
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

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
    claim_token: uuid.UUID
    """The database-minted fence this worker's acknowledgement must still match."""


class SearchEmbeddingQueue(Protocol):
    """Claim-fenced persistence for embedding work."""

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]: ...

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool: ...

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool: ...


class SearchEmbeddingService:
    """Drain changed search documents into pgvector."""

    def __init__(
        self,
        model: SearchEmbeddingModel,
        *,
        uow_factory: UnitOfWorkFactory[SearchEmbeddingUnitOfWork],
        max_attempts: int = 5,
    ) -> None:
        if max_attempts < 1:
            msg = tr(t"Embedding max_attempts must be positive.")
            raise InvalidStateError(msg)
        self._model = model
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts

    async def process_batch(self, *, limit: int = 8) -> tuple[int, int]:
        """Embed a bounded batch, retaining exhausted documents as dead letters."""
        if not 1 <= limit <= 32:
            msg = tr(t"Embedding claim limit must be between 1 and 32.")
            raise InvalidStateError(msg)
        succeeded = failed = 0
        async with self._uow_factory() as uow:
            jobs = await uow.search_embeddings.claim(limit=limit)
            await uow.commit()
        for job in jobs:
            try:
                # The model port is a Protocol, so the adapter's own timeout is not
                # something this loop can rely on. Without a bound here a hung
                # provider stalls the periodic job that awaits process_batch to
                # completion, staling its heartbeat and failing worker readiness.
                # TimeoutError lands in the handler below and retries the job.
                with anyio.fail_after(EMBEDDING_CALL_TIMEOUT_SECONDS):
                    embedding = await self._model.embed(job.text)
                embedding = _require_embedding(embedding)
                async with self._uow_factory() as uow:
                    applied = await uow.search_embeddings.complete(job, embedding, self._model.model_name)
                    await uow.commit()
                if applied:
                    succeeded += 1
            except asyncio.CancelledError:
                raise
            except Exception as error:
                async with self._uow_factory() as uow:
                    await uow.search_embeddings.fail(job, str(error), max_attempts=self._max_attempts)
                    await uow.commit()
                failed += 1
        return succeeded, failed


def _require_embedding(embedding: list[float] | None) -> list[float]:
    if embedding is None:
        msg = tr(t"The configured embedding provider did not return a vector.")
        raise InvalidStateError(msg)
    return embedding


class SearchEmbeddingUnitOfWork(UnitOfWork, Protocol):
    @property
    def search_embeddings(self) -> SearchEmbeddingQueue: ...


class SemanticCandidateService:
    """Generate the query vector before opening its database lookup scope."""

    def __init__(self, model: SearchEmbeddingModel, *, uow_factory: UnitOfWorkFactory[SearchVectorUnitOfWork]) -> None:
        self._model = model
        self._uow_factory = uow_factory

    async def candidates(self, query: str, *, limit: int) -> Sequence[SemanticCandidate]:
        embedding = await self._model.embed(query)
        if embedding is None:
            return ()
        async with self._uow_factory() as uow:
            return await uow.search_vectors.candidates(embedding, self._model.model_name, limit=limit)


class SearchVectorUnitOfWork(UnitOfWork, Protocol):
    @property
    def search_vectors(self) -> SearchVectorRepository: ...

"""Generate embeddings for queued search documents outside database transactions."""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import anyio

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.unit_of_work import UnitOfWork, UnitOfWorkFactory

EMBEDDING_CALL_TIMEOUT_SECONDS = 300.0
"""Maximum time for one embedding call, including the adapter's retries.

This exceeds the OpenAI adapter's timeout and retry budget so that it can report
its own failure before this service cancels the call.
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
    """Token identifying this claim; completion must not overwrite a later claim."""


class SearchEmbeddingQueue(Protocol):
    """Store embedding jobs and accept updates only from the current claim owner."""

    async def claim(self, *, limit: int) -> Sequence[SearchEmbeddingJob]: ...

    async def complete(self, job: SearchEmbeddingJob, embedding: list[float], model: str) -> bool: ...

    async def fail(self, job: SearchEmbeddingJob, error: str, *, max_attempts: int) -> bool: ...


class SearchEmbeddingService:
    """Generate vectors for queued documents and save them in separate transactions."""

    def __init__(
        self,
        model: SearchEmbeddingModel,
        *,
        uow_factory: UnitOfWorkFactory[SearchEmbeddingUnitOfWork],
        max_attempts: int = 5,
    ) -> None:
        if max_attempts < 1:
            msg = tr(t"search-embeddings-embedding-max = Embedding max_attempts must be positive.")
            raise InvalidStateError(msg)
        self._model = model
        self._uow_factory = uow_factory
        self._max_attempts = max_attempts

    async def process_batch(self, *, limit: int = 8) -> tuple[int, int]:
        """Claim and embed up to limit documents, returning (succeeded, failed).

        The claim is committed before calling the model. Each completion or failure
        is then committed separately. Success counts only vectors accepted by the
        queue; stale completions are ignored. Failure counts model or persistence
        exceptions, even if another worker has since claimed the job.

        Failed jobs are retried until max_attempts, then retained for inspection.
        Cancellation propagates without recording a failure.
        """
        if not 1 <= limit <= 32:
            msg = tr(t"search-embeddings-embedding-claim = Embedding claim limit must be between 1 and 32.")
            raise InvalidStateError(msg)
        succeeded = failed = 0
        async with self._uow_factory() as uow:
            jobs = await uow.search_embeddings.claim(limit=limit)
            await uow.commit()
        for job in jobs:
            try:
                # Bound even adapters without their own timeout. A hung call would
                # stall the worker's heartbeat; the handler below records the timeout
                # as a failed attempt so the job can be retried.
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
        msg = tr(t"search-embeddings-configured-embedding = The configured embedding provider did not return a vector.")
        raise InvalidStateError(msg)
    return embedding


class SearchEmbeddingUnitOfWork(UnitOfWork, Protocol):
    @property
    def search_embeddings(self) -> SearchEmbeddingQueue: ...

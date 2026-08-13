"""Embedding-provider and PostgreSQL vector-index adapters."""

import logging
from typing import Self

from openai import AsyncOpenAI, OpenAIError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from squid.builds.infrastructure.models import Build
from squid.config import EMBEDDING_DIMENSION, OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT_SECONDS, EmbeddingConfig
from squid.observability import add_counter, trace_span

logger = logging.getLogger(__name__)


class OpenAIEmbeddingModel:
    """Generate vectors through an OpenAI-compatible embeddings API."""

    def __init__(self, client: AsyncOpenAI | None, model: str, *, owns_client: bool = False) -> None:
        self._client = client
        self._model = model
        self._owns_client = owns_client

    @classmethod
    def from_config(cls, config: EmbeddingConfig) -> Self:
        """Create an embedding adapter from typed process configuration."""
        if not config.api_key:
            logger.warning("No OpenAI API key found; build embeddings are disabled.")
            return cls(None, config.model)
        return cls(
            AsyncOpenAI(
                base_url=str(config.base_url),
                api_key=config.api_key.get_secret_value(),
                # The SDK default is ten minutes with retries. The embedding job
                # runs inside a periodic loop that awaits it to completion, so a
                # hung provider would stale the heartbeat and fail readiness.
                timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
                max_retries=OPENAI_MAX_RETRIES,
            ),
            config.model,
            owns_client=True,
        )

    async def aclose(self) -> None:
        """Close the internally-owned provider client."""
        if self._client is not None and self._owns_client:
            await self._client.close()

    @property
    def model_name(self) -> str:
        """Return the provider model used to version persisted vectors."""
        return self._model

    async def embed(self, text: str) -> list[float] | None:
        if self._client is None:
            return None
        try:
            with trace_span(
                "openai.embedding",
                {"squid.provider.name": "openai-compatible", "squid.provider.operation": "embedding"},
            ):
                response = await self._client.embeddings.create(input=text, model=self._model)
        except OpenAIError:
            add_counter(
                "squid.provider.failures",
                attributes={
                    "squid.provider.name": "openai-compatible",
                    "squid.provider.operation": "embedding",
                },
            )
            logger.debug("Failed to generate embedding.", exc_info=True)
            return None
        embedding = response.data[0].embedding
        if len(embedding) != EMBEDDING_DIMENSION:
            logger.error(
                "Embedding provider returned the wrong vector dimension",
                extra={"squid.embedding.dimension": len(embedding), "squid.embedding.expected": EMBEDDING_DIMENSION},
            )
            return None
        return embedding


class PostgresBuildIndex:
    """Store and query build vectors in the authoritative PostgreSQL row."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        if len(embedding) != EMBEDDING_DIMENSION:
            msg = f"Build embedding must contain exactly {EMBEDDING_DIMENSION} values."
            raise ValueError(msg)
        async with self._session_factory.begin() as session:
            await session.execute(update(Build).where(Build.id == build_id).values(embedding=embedding))

    async def find_nearest(self, embedding: list[float]) -> int | None:
        if len(embedding) != EMBEDDING_DIMENSION:
            return None
        statement = (
            select(Build.id)
            .where(Build.embedding.is_not(None))
            .order_by(Build.embedding.cosine_distance(embedding))
            .limit(1)
        )
        async with self._session_factory() as session:
            return await session.scalar(statement)

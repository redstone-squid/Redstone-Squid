"""External embedding-model and vector-index adapters."""

import asyncio
import logging
from typing import Self

import vecs
from openai import AsyncOpenAI, OpenAIError

from squid.config import EMBEDDING_DIMENSION, EmbeddingConfig

logger = logging.getLogger(__name__)


class OpenAIEmbeddingModel:
    """Generate vectors through an OpenAI-compatible embeddings API."""

    def __init__(self, client: AsyncOpenAI | None, model: str) -> None:
        self._client = client
        self._model = model

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
            ),
            config.model,
        )

    async def embed(self, text: str) -> list[float] | None:
        if self._client is None:
            return None
        try:
            response = await self._client.embeddings.create(input=text, model=self._model)
        except OpenAIError:
            logger.debug("Failed to generate embedding.", exc_info=True)
            return None
        return response.data[0].embedding


class VecsBuildIndex:
    """Store and query build vectors without blocking the event loop."""

    def __init__(self, connection: str | None) -> None:
        self._connection = connection

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        if self._connection is None:
            logger.warning("No SQUID_VECTOR_DATABASE_URL configured; skipping build vector indexing.")
            return
        await asyncio.to_thread(self._upsert, build_id, embedding)

    def _upsert(self, build_id: int, embedding: list[float]) -> None:
        assert self._connection is not None
        client = vecs.create_client(self._connection)
        try:
            collection = client.get_or_create_collection(name="builds", dimension=EMBEDDING_DIMENSION)
            collection.upsert(records=[(str(build_id), embedding, {})])
        finally:
            client.disconnect()

    async def find_nearest(self, embedding: list[float]) -> int | None:
        if self._connection is None:
            return None
        result = await asyncio.to_thread(self._find_nearest, embedding)
        return int(result) if result is not None else None

    def _find_nearest(self, embedding: list[float]) -> str | None:
        assert self._connection is not None
        client = vecs.create_client(self._connection)
        try:
            collection = client.get_or_create_collection(name="builds", dimension=EMBEDDING_DIMENSION)
            result = collection.query(embedding, limit=1)
            return str(result[0]) if result else None
        finally:
            client.disconnect()

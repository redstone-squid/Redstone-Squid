"""External embedding-model and vector-index adapters."""

import asyncio
import logging
import os
from typing import Self

import vecs
from openai import AsyncOpenAI, OpenAIError

logger = logging.getLogger(__name__)


class OpenAIEmbeddingModel:
    """Generate vectors through an OpenAI-compatible embeddings API."""

    def __init__(self, client: AsyncOpenAI | None, model: str) -> None:
        self._client = client
        self._model = model

    @classmethod
    def from_environment(cls) -> Self:
        """Create an embedding adapter from process configuration."""
        api_key = os.environ.get("EMBEDDING_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
        if not api_key:
            logger.warning("No OpenAI API key found; build embeddings are disabled.")
            return cls(None, model)
        return cls(
            AsyncOpenAI(
                base_url=os.environ.get("EMBEDDING_OPENAI_BASE_URL") or os.environ.get("OPENAI_BASE_URL"),
                api_key=api_key,
            ),
            model,
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

    def __init__(self, connection: str | None, *, dimension: int) -> None:
        self._connection = connection
        self._dimension = dimension

    @classmethod
    def from_environment(cls) -> Self:
        """Create a vecs index from process configuration."""
        return cls(
            os.environ.get("DB_CONNECTION"),
            dimension=int(os.environ.get("EMBEDDING_DIMENSION", "1536")),
        )

    async def upsert(self, build_id: int, embedding: list[float]) -> None:
        if self._connection is None:
            logger.warning("No DB_CONNECTION configured; skipping build vector indexing.")
            return
        await asyncio.to_thread(self._upsert, build_id, embedding)

    def _upsert(self, build_id: int, embedding: list[float]) -> None:
        assert self._connection is not None
        client = vecs.create_client(self._connection)
        try:
            collection = client.get_or_create_collection(name="builds", dimension=self._dimension)
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
            collection = client.get_or_create_collection(name="builds", dimension=self._dimension)
            result = collection.query(embedding, limit=1)
            return str(result[0]) if result else None
        finally:
            client.disconnect()

"""External vector-search adapter for builds."""

import asyncio
import os

import vecs
from openai import AsyncOpenAI


class VecsBuildSearch:
    """Look up the nearest build embedding without blocking the event loop."""

    async def find_build_id(self, query: str) -> int | None:
        response = await AsyncOpenAI().embeddings.create(input=query, model="text-embedding-3-small")
        query_vector = response.data[0].embedding

        def query_vecs() -> list[str]:
            client = vecs.create_client(os.environ["DB_CONNECTION"])
            collection = client.get_or_create_collection(name="builds", dimension=1536)
            return collection.query(query_vector, limit=1)  # pyright: ignore[reportReturnType]

        result = await asyncio.to_thread(query_vecs)
        return int(result[0]) if result else None

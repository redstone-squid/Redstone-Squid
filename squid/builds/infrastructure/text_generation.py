"""OpenAI-compatible text generation adapter."""

import logging
import os
from typing import Self

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class OpenAITextGenerator:
    """Generate text through an OpenAI-compatible chat API."""

    def __init__(self, client: AsyncOpenAI | None) -> None:
        self._client = client

    @classmethod
    def from_environment(cls) -> Self:
        """Create an adapter from process configuration."""
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("No OpenAI API key found; build inference is disabled.")
            return cls(None)
        return cls(
            AsyncOpenAI(
                base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                api_key=api_key,
            )
        )

    async def generate(self, prompt: str, *, model: str) -> str | None:
        if self._client is None:
            return None
        completion = await self._client.beta.chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content

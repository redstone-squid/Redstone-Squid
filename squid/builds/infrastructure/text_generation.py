"""OpenAI-compatible text generation adapter."""

import logging

from openai import AsyncOpenAI

from squid.config import OpenAIConfig

logger = logging.getLogger(__name__)


class OpenAITextGenerator:
    """Generate text through an OpenAI-compatible chat API."""

    def __init__(self, client: AsyncOpenAI | None) -> None:
        self._client = client

    @classmethod
    def from_config(cls, config: OpenAIConfig) -> "OpenAITextGenerator":
        """Create an adapter from typed process configuration."""
        if not config.api_key:
            logger.warning("No OpenAI API key found; build inference is disabled.")
            return cls(None)
        return cls(
            AsyncOpenAI(
                base_url=str(config.base_url),
                api_key=config.api_key.get_secret_value(),
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

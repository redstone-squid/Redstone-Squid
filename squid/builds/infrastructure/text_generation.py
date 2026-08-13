"""OpenAI-compatible structured text generation adapter."""

import base64
import logging
import re
from collections.abc import Sequence
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletion
from pydantic import BaseModel

from squid.builds.application.inference import InlineImage
from squid.config import OPENAI_MAX_RETRIES, OPENAI_REQUEST_TIMEOUT_SECONDS, OpenAIConfig
from squid.observability import add_counter, trace_span

logger = logging.getLogger(__name__)


class OpenAITextGenerator:
    """Generate structured responses through an OpenAI-compatible chat API."""

    def __init__(self, client: AsyncOpenAI | None, *, owns_client: bool = False) -> None:
        self._client = client
        self._owns_client = owns_client

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
                # The SDK default is ten minutes with retries, which is far longer
                # than any interactive Discord surface is willing to wait.
                timeout=OPENAI_REQUEST_TIMEOUT_SECONDS,
                max_retries=OPENAI_MAX_RETRIES,
            ),
            owns_client=True,
        )

    async def aclose(self) -> None:
        """Close the internally-owned provider client."""
        if self._client is not None and self._owns_client:
            await self._client.close()

    async def generate[T: BaseModel](
        self,
        system: str,
        user: str,
        schema: type[T],
        *,
        model: str,
        images: Sequence[InlineImage] = (),
        reasoning_effort: str | None = None,
    ) -> T | None:
        """Generate a schema-validated response, with a compatibility fallback."""
        if self._client is None:
            return None

        content: list[dict[str, Any]] = [{"type": "text", "text": user}]
        for inline_image in images:
            encoded = base64.b64encode(inline_image.data).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{inline_image.content_type};base64,{encoded}"},
                }
            )
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
            "response_format": schema,
        }
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort
        try:
            with trace_span(
                "openai.chat",
                {"squid.provider.name": "openai-compatible", "squid.provider.operation": "inference"},
            ):
                completion = await self._client.chat.completions.parse(**kwargs)
            return cast(T | None, completion.choices[0].message.parsed)
        except openai.BadRequestError:
            logger.warning("Provider rejected strict structured output; retrying with JSON instructions")
        except openai.OpenAIError:
            add_counter(
                "squid.provider.failures",
                attributes={
                    "squid.provider.name": "openai-compatible",
                    "squid.provider.operation": "inference",
                },
            )
            raise

        fallback_system = f"{system}\n\nReturn only JSON matching this schema:\n{schema.model_json_schema()}"
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["messages"] = [
            {"role": "system", "content": fallback_system},
            {"role": "user", "content": content},
        ]
        fallback_kwargs.pop("response_format")
        try:
            with trace_span(
                "openai.chat.fallback",
                {"squid.provider.name": "openai-compatible", "squid.provider.operation": "inference"},
            ):
                # Splatting `dict[str, Any]` collapses overload resolution, so the checker also
                # sees the streaming return. `stream` is never set here, so it is always a
                # complete response.
                raw_completion = await self._client.chat.completions.create(**fallback_kwargs)
                completion = cast(ChatCompletion, raw_completion)
        except openai.OpenAIError:
            add_counter(
                "squid.provider.failures",
                attributes={
                    "squid.provider.name": "openai-compatible",
                    "squid.provider.operation": "inference",
                },
            )
            raise
        raw = completion.choices[0].message.content
        if raw is None:
            return None
        stripped = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.IGNORECASE)
        return schema.model_validate_json(stripped)

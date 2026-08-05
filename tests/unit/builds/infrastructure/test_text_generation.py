"""Structured OpenAI text-generation adapter tests."""

from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest
from openai import AsyncOpenAI
from pytest_mock import MockerFixture

from squid.builds.application import InferenceResult, InlineImage
from squid.builds.infrastructure.text_generation import OpenAITextGenerator


class FakeCompletions:
    def __init__(self, *, reject_strict: bool = False, fail: bool = False) -> None:
        self.reject_strict = reject_strict
        self.fail = fail
        self.parse_kwargs: dict[str, Any] = {}
        self.create_kwargs: dict[str, Any] = {}

    async def parse(self, **kwargs: Any) -> SimpleNamespace:
        self.parse_kwargs = kwargs
        if self.fail:
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid/chat"))
        if self.reject_strict:
            response = httpx.Response(400, request=httpx.Request("POST", "https://example.invalid/chat"))
            raise openai.BadRequestError("unsupported", response=response, body=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=InferenceResult(builds=[])))])

    async def create(self, **kwargs: Any) -> SimpleNamespace:
        self.create_kwargs = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='```json\n{"builds": []}\n```'))]
        )


def fake_client(completions: FakeCompletions) -> AsyncOpenAI:
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return cast(AsyncOpenAI, client)


async def test_generate_passes_schema_reasoning_and_inline_images() -> None:
    completions = FakeCompletions()
    generator = OpenAITextGenerator(fake_client(completions))
    image = InlineImage(b"png", "image/png", 1, "attachment")

    result = await generator.generate(
        "system", "user", InferenceResult, model="model", images=[image], reasoning_effort="low"
    )

    assert result == InferenceResult(builds=[])
    assert completions.parse_kwargs["response_format"] is InferenceResult
    assert completions.parse_kwargs["reasoning_effort"] == "low"
    messages = completions.parse_kwargs["messages"]
    assert messages[1]["content"][1]["image_url"]["url"] == "data:image/png;base64,cG5n"


async def test_generate_falls_back_to_lenient_json() -> None:
    completions = FakeCompletions(reject_strict=True)
    generator = OpenAITextGenerator(fake_client(completions))

    result = await generator.generate("system", "user", InferenceResult, model="model")

    assert result == InferenceResult(builds=[])
    assert "response_format" not in completions.create_kwargs
    assert "Return only JSON matching this schema" in completions.create_kwargs["messages"][0]["content"]


async def test_generate_counts_provider_failures(mocker: MockerFixture) -> None:
    generator = OpenAITextGenerator(fake_client(FakeCompletions(fail=True)))
    counter = mocker.patch("squid.builds.infrastructure.text_generation.add_counter")

    with pytest.raises(openai.APIConnectionError):
        await generator.generate("system", "user", InferenceResult, model="model")

    counter.assert_called_once_with(
        "squid.provider.failures",
        attributes={
            "squid.provider.name": "openai-compatible",
            "squid.provider.operation": "inference",
        },
    )

"""Mojang account adapter validation tests."""

from typing import Any, cast
from unittest.mock import Mock
from uuid import UUID

import aiohttp
import pytest

from squid.accounts.errors import MinecraftServiceUnavailableError
from squid.accounts.infrastructure.mojang import MojangClient


class FakeContent:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def read(self, _limit: int) -> bytes:
        return self._payload


class FakeResponse:
    def __init__(self, status: int, payload: bytes = b"") -> None:
        self.status = status
        self.content_length = len(payload)
        self.content = FakeContent(payload)


class FakeRequest:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> Any:
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        return None


async def test_mojang_client_validates_a_bounded_profile_response() -> None:
    session = Mock()
    session.get.return_value = FakeRequest(FakeResponse(200, b'{"name":"Builder"}'))
    client = MojangClient(cast(aiohttp.ClientSession, session))

    username = await client.get_username(UUID("12345678-1234-5678-1234-567812345678"))

    assert username == "Builder"
    assert session.get.call_args.kwargs == {"allow_redirects": False}


async def test_mojang_client_rejects_malformed_success_payloads() -> None:
    session = Mock()
    session.get.return_value = FakeRequest(FakeResponse(200, b'{"unexpected":true}'))
    client = MojangClient(cast(aiohttp.ClientSession, session))

    with pytest.raises(MinecraftServiceUnavailableError):
        await client.get_username(UUID("12345678-1234-5678-1234-567812345678"))

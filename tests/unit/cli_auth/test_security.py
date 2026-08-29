"""CLI bearer authentication at the shared API security boundary."""

from dataclasses import dataclass
from typing import cast
from uuid import UUID

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.types import Scope

from squid.api.security import current_caller
from squid.cli_auth.application import CliAuthorizationService
from squid.cli_auth.domain import CliIdentity
from squid.cli_auth.errors import InvalidCliSessionError
from squid.core.errors import AuthenticationError
from tests.unit.api.fakes import TEST_CONFIG

DEVICE_ID = UUID("ea252a1c-0bcd-47f7-84d8-36e6801eb374")
SESSION_ID = UUID("f5f51999-37c1-4a85-9d7e-f53875428f99")
TOKEN = f"squid_cli_v1_{SESSION_ID.hex}_{'t' * 43}"


class FakeCliAuthorization(CliAuthorizationService):
    def __init__(self, *, valid: bool = True) -> None:
        self.valid = valid
        self.token: str | None = None

    async def authenticate(self, token: str) -> CliIdentity:
        self.token = token
        if not self.valid:
            raise InvalidCliSessionError
        return CliIdentity(
            account_id=42,
            device_id=DEVICE_ID,
            session_id=SESSION_ID,
            consent_pending=False,
        )


def request_with_service(cli: FakeCliAuthorization) -> Request:
    @dataclass(frozen=True, slots=True)
    class Services:
        cli_authorization: CliAuthorizationService
        minecraft_player_authorization: None = None
        minecraft_installations: None = None
        api_keys: None = None

    @dataclass(frozen=True, slots=True)
    class Runtime:
        services: Services

    app = FastAPI()
    app.state.config = TEST_CONFIG
    app.state.runtime = Runtime(Services(cli))
    return Request(cast(Scope, {
            "type": "http",
            "method": "GET",
            "path": "/v1/submissions/drafts",
            "headers": [],
            "app": app,
        }))


async def test_cli_token_derives_account_device_and_session_caller() -> None:
    cli = FakeCliAuthorization()

    caller = await current_caller(request_with_service(cli), f"Bearer {TOKEN}")

    assert caller.kind == "cli"
    assert caller.account_id == 42
    assert caller.cli_device_id == DEVICE_ID
    assert caller.cli_session_id == SESSION_ID
    assert caller.subject == f"cli-session:{SESSION_ID}"
    assert cli.token == TOKEN


async def test_invalid_cli_token_does_not_fall_through_to_api_key_authentication() -> None:
    with pytest.raises(AuthenticationError):
        await current_caller(request_with_service(FakeCliAuthorization(valid=False)), f"Bearer {TOKEN}")

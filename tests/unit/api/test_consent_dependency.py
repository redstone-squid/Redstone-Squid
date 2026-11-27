"""Every consent-gated entry point answers with the same two failures."""

import inspect
from collections.abc import Callable
from typing import Literal

import pytest

from squid.api.security import Caller, current_browser_account_id
from squid.api.v1.submissions import _submission_origin
from squid.core.errors import AuthenticationError
from tests.unit.api.fakes import credential_nodes

ANONYMOUS = Caller(kind="anonymous", subject="anonymous", nodes=credential_nodes())


def account() -> Caller:
    return Caller(
        kind="account",
        subject="account:1",
        nodes=credential_nodes("build.submission.create"),
        account_id=1,
    )


GATES: list[tuple[str, Callable[[Caller], object]]] = [
    ("security", Caller.require_account_id),
    ("submissions", _submission_origin),
    ("browser_auth", current_browser_account_id),
]
"""Both dependency and direct-call forms must report the same consent refusal."""


async def call(gate: Callable[[Caller], object], caller: Caller) -> object:
    result = gate(caller)
    return await result if inspect.isawaitable(result) else result


@pytest.mark.parametrize(("name", "gate"), GATES, ids=[name for name, _ in GATES])
async def test_ordinary_writes_need_no_privacy_receipt(name: str, gate: Callable[[Caller], object]) -> None:
    del name
    await call(gate, account())


@pytest.mark.parametrize(("name", "gate"), GATES, ids=[name for name, _ in GATES])
async def test_an_anonymous_caller_is_refused_before_consent_is_considered(
    name: str, gate: Callable[[Caller], object]
) -> None:
    """Missing authentication takes precedence over missing consent."""
    del name
    with pytest.raises(AuthenticationError):
        await call(gate, ANONYMOUS)


@pytest.mark.parametrize("kind", ["service", "account", "cli", "minecraft_player"])
def test_every_owned_credential_requires_current_consent(
    kind: Literal["service", "account", "cli", "minecraft_player"],
) -> None:
    caller = Caller(kind=kind, subject="owned", account_id=1)
    assert caller.require_account_id() == 1


@pytest.mark.parametrize("kind", ["service", "cli", "minecraft_player"])
async def test_browser_gate_rejects_non_browser_credentials_even_with_an_account(
    kind: Literal["service", "cli", "minecraft_player"],
) -> None:
    caller = Caller(kind=kind, subject=f"{kind}:1", account_id=1)
    with pytest.raises(AuthenticationError):
        await current_browser_account_id(caller)

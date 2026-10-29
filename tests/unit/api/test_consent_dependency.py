"""Every consent-gated entry point answers with the same two failures."""

import inspect
from collections.abc import Callable
from typing import Literal

import pytest

from squid.accounts.errors import ConsentRequiredError
from squid.api.security import Caller, current_browser_account_id, require_consented_account
from squid.api.v1.submissions import _submission_actor
from squid.core.errors import AuthenticationError
from tests.unit.api.fakes import credential_nodes

ANONYMOUS = Caller(kind="anonymous", subject="anonymous", nodes=credential_nodes())


def account(*, consent_pending: bool) -> Caller:
    return Caller(
        kind="account",
        subject="account:1",
        nodes=credential_nodes("build.submission.create"),
        account_id=1,
        consent_pending=consent_pending,
    )


GATES: list[tuple[str, Callable[[Caller], object]]] = [
    ("security", require_consented_account),
    ("submissions", _submission_actor),
    ("browser_auth", current_browser_account_id),
]
"""Both dependency and direct-call forms must report the same consent refusal."""


async def call(gate: Callable[[Caller], object], caller: Caller) -> object:
    result = gate(caller)
    return await result if inspect.isawaitable(result) else result


@pytest.mark.parametrize(("name", "gate"), GATES, ids=[name for name, _ in GATES])
async def test_a_consent_pending_caller_is_told_how_to_consent(name: str, gate: Callable[[Caller], object]) -> None:
    """One shared gate is only worth having if every surface reports it identically."""
    del name
    with pytest.raises(ConsentRequiredError) as error:
        await call(gate, account(consent_pending=True))

    assert error.value.public_context == {
        "consent_url": "/v1/users/me/consent",
        "notice_url": "/v1/consent/notice",
    }


@pytest.mark.parametrize(("name", "gate"), GATES, ids=[name for name, _ in GATES])
async def test_an_anonymous_caller_is_refused_before_consent_is_considered(
    name: str, gate: Callable[[Caller], object]
) -> None:
    """401 has to win over 400: "who are you" precedes "have you agreed"."""
    del name
    with pytest.raises(AuthenticationError):
        await call(gate, ANONYMOUS)


def test_a_consented_caller_passes_the_gate() -> None:
    assert require_consented_account(account(consent_pending=False)) == 1


@pytest.mark.parametrize("kind", ["service", "cli", "minecraft_player"])
async def test_browser_gate_rejects_non_browser_credentials_even_with_an_account(
    kind: Literal["service", "cli", "minecraft_player"],
) -> None:
    caller = Caller(kind=kind, subject=f"{kind}:1", account_id=1)
    with pytest.raises(AuthenticationError):
        await current_browser_account_id(caller)

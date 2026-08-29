"""Browser-session service tests, over Discord and over a second provider alike."""

from dataclasses import dataclass, field
from typing import override

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr
from whenever import Instant

from squid.accounts.application import AccountService
from squid.accounts.domain import Account, AccountConsent, IdentityProvider
from squid.auth.application.providers import DiscordOAuthProvider, OAuthProvider
from squid.auth.application.web import WebSessionService
from squid.config import OAuthClientCredentials, OAuthConfig, UpstreamHttpConfig
from squid.core.errors import AuthenticationError, NotFoundError
from tests.unit.auth.application.fakes import FakeXboxOAuthProvider, SessionRepository

NOW = Instant.from_utc(2026, 8, 5)


@dataclass(slots=True)
class AccountServiceRecorder(AccountService):
    account_id: int = 42
    identities: list[tuple[IdentityProvider, str]] = field(default_factory=list)

    @override
    async def get_or_create_identity(
        self, provider: IdentityProvider, subject: str, *, consent: AccountConsent | None = None
    ) -> Account:
        del consent
        self.identities.append((provider, subject))
        return Account(id=self.account_id)


def credentials() -> OAuthClientCredentials:
    return OAuthClientCredentials(
        client_id="client",
        client_secret=SecretStr("secret"),
        redirect_uri=AnyHttpUrl("https://api.example/v1/auth/discord/callback"),
    )


def discord_provider(
    transport: httpx.MockTransport | None = None,
    *,
    upstreams: UpstreamHttpConfig | None = None,
) -> DiscordOAuthProvider:
    resolved = transport or httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"access_token": "discord-token"} if request.url.path.endswith("/oauth2/token") else {"id": "123"},
        )
    )
    return DiscordOAuthProvider(credentials(), httpx.AsyncClient(transport=resolved), upstreams=upstreams)


def service(repository: SessionRepository, *providers: OAuthProvider) -> WebSessionService:
    return WebSessionService(
        repository,
        AccountServiceRecorder(),
        {provider.slug: provider for provider in providers},
        336,
        "session-pepper-for-tests",
        now=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_oauth_state_is_durable_and_callback_issues_hashed_session() -> None:
    repository = SessionRepository()
    web = service(repository, discord_provider())

    authorize_url = await web.authorize_url("discord", "/account")
    assert repository.state is not None
    assert "code_challenge_method=S256" in authorize_url

    token, redirect_to = await web.callback("discord", "code", repository.state.state, user_agent="test")

    assert redirect_to == "/account"
    assert repository.token_hash is not None
    assert repository.token_hash == web.hash_token(token)
    assert token.encode() not in repository.token_hash


async def test_oauth_service_uses_configured_loopback_endpoints() -> None:
    repository = SessionRepository()
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = {"access_token": "discord-token"} if request.url.path.endswith("/oauth2/token") else {"id": "123"}
        return httpx.Response(200, json=payload)

    provider = discord_provider(
        httpx.MockTransport(handler),
        upstreams=UpstreamHttpConfig.model_validate(
            {
                "discord_api_url": "http://127.0.0.1:8102/discord/api",
                "discord_authorize_url": "http://127.0.0.1:8102/discord/authorize",
            }
        ),
    )
    web = service(repository, provider)

    authorize_url = await web.authorize_url("discord", None)
    assert authorize_url.startswith("http://127.0.0.1:8102/discord/authorize?")
    assert repository.state is not None
    await web.callback("discord", "code", repository.state.state, user_agent=None)

    assert [request.url.path for request in requests] == [
        "/discord/api/oauth2/token",
        "/discord/api/users/@me",
    ]


async def test_a_second_provider_logs_in_and_lands_its_own_identity_namespace() -> None:
    """The seam proof: nothing on the session path assumes Discord.

    A full browser login through a provider that is not Discord issues a session and
    resolves an account under that provider's own namespace.
    """
    repository = SessionRepository()
    fake = FakeXboxOAuthProvider()
    accounts = AccountServiceRecorder(account_id=7)
    web = WebSessionService(repository, accounts, {fake.slug: fake}, 336, "session-pepper-for-tests", now=lambda: NOW)

    authorize_url = await web.authorize_url("bedrock", "/account")
    assert authorize_url.startswith("https://xbox.example/authorize?")
    assert repository.state is not None
    assert repository.state.provider is IdentityProvider.BEDROCK
    minted_verifier = repository.state.code_verifier

    token, redirect_to = await web.callback("bedrock", "code", repository.state.state, user_agent=None)

    assert accounts.identities == [(IdentityProvider.BEDROCK, "2535465049322445")]
    assert fake.exchanges == [("code", minted_verifier)]
    assert redirect_to == "/account"
    assert repository.account_id == 7
    assert repository.token_hash == web.hash_token(token)


async def test_a_state_minted_for_one_provider_is_not_redeemable_at_another() -> None:
    """The IdP mix-up class: without the check, a state minted at A is spendable at B."""
    repository = SessionRepository()
    web = service(repository, discord_provider(), FakeXboxOAuthProvider())

    await web.authorize_url("discord", None)
    assert repository.state is not None

    with pytest.raises(AuthenticationError):
        await web.callback("bedrock", "code", repository.state.state, user_agent=None)


async def test_an_unknown_provider_slug_is_not_found_rather_than_unauthorized() -> None:
    """ "This deployment has no GitHub login" is a fact about the resource, not a credential failure."""
    web = service(SessionRepository(), discord_provider())

    with pytest.raises(NotFoundError):
        await web.authorize_url("github", None)
    with pytest.raises(NotFoundError):
        await web.callback("github", "code", "state", user_agent=None)


def test_configured_clients_are_reported_per_provider() -> None:
    complete = OAuthConfig(
        discord_client_id="client",
        discord_client_secret=SecretStr("secret"),
        redirect_uri=AnyHttpUrl("https://api.example/v1/auth/discord/callback"),
    )
    assert set(complete.clients()) == {IdentityProvider.DISCORD}
    assert OAuthConfig().clients() == {}


def test_partial_credentials_are_rejected_rather_than_silently_disabling_login() -> None:
    with pytest.raises(ValueError, match="configured together"):
        OAuthConfig(discord_client_id="client")


def test_a_deployment_with_no_providers_reports_itself_unconfigured() -> None:
    web = WebSessionService(SessionRepository(), AccountServiceRecorder(), {}, 336, "pepper", now=lambda: NOW)

    assert web.configured is False

"""Exchange OAuth authorization codes for verified provider identities."""

from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlencode

import httpx

from squid.accounts.domain import IdentityDetails, IdentityProvider
from squid.config import OAuthClientCredentials, UpstreamHttpConfig
from squid.core.errors import ServiceUnavailableError


class OAuthProvider(Protocol):
    """One external authorization-code identity source."""

    slug: str
    """The URL segment this provider is reached at, e.g. "discord"."""
    provider: IdentityProvider
    """The identity namespace a successful exchange lands in."""

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        """The URL to send the browser to, carrying this provider's own scopes."""
        ...

    async def fetch_identity(self, *, code: str, code_verifier: str) -> IdentityDetails:
        """Redeem an authorization code and return the verified provider subject.

        The adapter keeps the provider access token private. It uses the token to
        fetch identity details when needed, then returns only the identity.
        """
        ...


class DiscordOAuthProvider:
    """Discord's authorization-code flow, reading `/users/@me` for the snowflake."""

    slug = "discord"
    provider = IdentityProvider.DISCORD

    def __init__(
        self,
        credentials: OAuthClientCredentials,
        client: httpx.AsyncClient,
        *,
        upstreams: UpstreamHttpConfig | None = None,
    ) -> None:
        self._credentials = credentials
        self._client = client
        resolved = upstreams or UpstreamHttpConfig()
        self._api_url = str(resolved.discord_api_url).rstrip("/")
        self._authorize_url = str(resolved.discord_authorize_url).rstrip("/")

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        # OAuth scopes belong to the provider; other providers use different names.
        query = urlencode(
            {
                "client_id": self._credentials.client_id,
                "redirect_uri": str(self._credentials.redirect_uri),
                "response_type": "code",
                "scope": "identify",
                "state": state,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{self._authorize_url}?{query}"

    async def fetch_identity(self, *, code: str, code_verifier: str) -> IdentityDetails:
        try:
            token_response = await self._client.post(
                f"{self._api_url}/oauth2/token",
                data={
                    "client_id": self._credentials.client_id,
                    "client_secret": self._credentials.client_secret.get_secret_value(),
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": str(self._credentials.redirect_uri),
                    "code_verifier": code_verifier,
                },
            )
            if token_response.status_code != 200:
                raise _unavailable()
            access_token = token_response.json()["access_token"]
            identity_response = await self._client.get(
                f"{self._api_url}/users/@me", headers={"Authorization": f"Bearer {access_token}"}
            )
            if identity_response.status_code != 200:
                raise _unavailable()
            payload = identity_response.json()
            return IdentityDetails(self.provider, str(int(payload["id"])), payload.get("username"))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise _unavailable() from error


def _unavailable() -> ServiceUnavailableError:
    msg = "Discord OAuth exchange failed."
    return ServiceUnavailableError(msg, resource="discord")


PROVIDER_FACTORIES: Mapping[IdentityProvider, Callable[..., OAuthProvider]] = {
    IdentityProvider.DISCORD: DiscordOAuthProvider,
}
"""Provider adapters available to the application.

Bootstrap enables only entries with complete OAuthConfig credentials.
"""

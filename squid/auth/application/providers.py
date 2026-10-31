"""Authorization-code adapters, one per external identity source.

A module rather than a package: there is one real adapter and one test fake, and three
files would hold what fits on a screen. Add a second real provider here.
"""

from collections.abc import Callable, Mapping
from typing import Protocol
from urllib.parse import urlencode

import httpx

from squid.accounts.domain import IdentityProvider
from squid.auth.domain.oauth import ExternalIdentity
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

    async def fetch_identity(self, *, code: str, code_verifier: str) -> ExternalIdentity:
        """Redeem one authorization code and return the subject it proves.

        The token exchange is deliberately swallowed rather than exposed as a separate
        step. The access token has exactly one use -- reading the profile endpoint -- and
        no caller outside the adapter should ever hold it; splitting the two would force
        it through `WebSessionService`, which would then have to decide whether to log,
        store, or drop it, and the answer is always "drop it". An OIDC provider whose
        token response already carries an `id_token` makes no second request here.
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
        # `scope` is adapter-private rather than a Protocol member: hoisting it would imply
        # providers share a scope vocabulary, which they do not.
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

    async def fetch_identity(self, *, code: str, code_verifier: str) -> ExternalIdentity:
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
            return ExternalIdentity(self.provider, str(int(payload["id"])), payload.get("username"))
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
            raise _unavailable() from error


def _unavailable() -> ServiceUnavailableError:
    msg = "Discord OAuth exchange failed."
    return ServiceUnavailableError(msg, resource="discord")


PROVIDER_FACTORIES: Mapping[IdentityProvider, Callable[..., OAuthProvider]] = {
    IdentityProvider.DISCORD: DiscordOAuthProvider,
}
"""Every identity namespace this deployment can log in through.

Bootstrap intersects this with the providers `OAuthConfig.clients()` has complete
credentials for, so adding a real provider is one entry here, one adapter class above,
and three flat config fields.
"""

"""Doubles for the browser-session service.

`FakeXboxOAuthProvider` claims `IdentityProvider.BEDROCK` rather than a test-only enum
member: a `TEST` member would pollute the production `match` exhaustiveness that
`AccountIdentity.for_provider` depends on, and reusing `DISCORD` would prove only the
slug routing, not that a second namespace lands correctly in `account_identities`.
Bedrock is a provider this project would plausibly add for real and already has subject
validation.
"""

from whenever import Instant

from squid.accounts.domain import IdentityProvider
from squid.auth.domain.oauth import ExternalIdentity
from squid.auth.domain.sessions import OAuthState


class SessionRepository:
    """Small stateful repository fake for the authorization-code exchange."""

    def __init__(self) -> None:
        self.state: OAuthState | None = None
        self.token_hash: bytes | None = None
        self.account_id: int | None = None

    async def save_state(self, state: OAuthState) -> None:
        self.state = state

    async def consume_state(self, state: str, *, now: Instant) -> OAuthState | None:
        if self.state is None or self.state.state != state or self.state.expires_at <= now:
            return None
        saved, self.state = self.state, None
        return saved

    async def create_session(
        self,
        *,
        token_hash: bytes,
        account_id: int,
        expires_at: Instant,
        user_agent: str | None,
    ) -> str:
        del expires_at, user_agent
        self.token_hash = token_hash
        self.account_id = account_id
        return "session-id"

    async def authenticate(self, token_hash: bytes, *, now: Instant) -> None:
        del token_hash, now
        return

    async def revoke(self, token_hash: bytes, *, now: Instant) -> None:
        del token_hash, now


class FakeXboxOAuthProvider:
    """A second provider, proving nothing on the session path assumes Discord."""

    slug = "bedrock"
    provider = IdentityProvider.BEDROCK

    def __init__(self, *, subject: str = "2535465049322445") -> None:
        self._subject = subject
        self.exchanges: list[tuple[str, str]] = []

    def authorize_url(self, *, state: str, code_challenge: str) -> str:
        return f"https://xbox.example/authorize?state={state}&code_challenge={code_challenge}"

    async def fetch_identity(self, *, code: str, code_verifier: str) -> ExternalIdentity:
        self.exchanges.append((code, code_verifier))
        return ExternalIdentity(self.provider, self._subject, "Gamertag")

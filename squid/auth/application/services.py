"""API-key issuance and authentication."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterable

from whenever import Instant

from squid.auth.application.ports import ApiKeyRepository
from squid.auth.domain import ApiKey, IssuedApiKey

API_KEY_PREFIX = "sq"
API_KEY_SECRET_BYTES = 32
LAST_USED_WRITE_INTERVAL_SECONDS = 60


class ApiKeyService:
    """Issue and verify high-entropy service credentials."""

    def __init__(
        self,
        repository: ApiKeyRepository,
        pepper: str | bytes,
        *,
        now: Callable[[], Instant] = Instant.now,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._repository = repository
        self._pepper = pepper.encode() if isinstance(pepper, str) else pepper
        if not self._pepper:
            msg = "API key pepper must not be empty."
            raise ValueError(msg)
        self._now = now
        self._token_bytes = token_bytes

    async def issue(
        self,
        *,
        label: str,
        scopes: Iterable[str],
        owner_account_id: int | None = None,
        created_by_account_id: int | None = None,
        expires_at: Instant | None = None,
    ) -> IssuedApiKey:
        """Create a credential, returning its plaintext token exactly once."""
        key_id = self._urlsafe_token(12)
        secret = self._urlsafe_token(API_KEY_SECRET_BYTES)
        key = await self._repository.add(
            key_id=key_id,
            secret_hash=self.hash_secret(secret),
            label=label,
            scopes=frozenset(scopes),
            owner_account_id=owner_account_id,
            created_by_account_id=created_by_account_id,
            expires_at=expires_at,
        )
        return IssuedApiKey(key=key, token=f"{API_KEY_PREFIX}_{key_id}_{secret}")

    async def authenticate(self, token: str, *, used_ip: str | None = None) -> ApiKey | None:
        """Return the active key matching *token*, or ``None`` for invalid credentials."""
        parsed = self._parse_token(token)
        if parsed is None:
            return None
        key_id, secret = parsed
        key = await self._repository.get_by_key_id(key_id)
        if key is None or not hmac.compare_digest(key.secret_hash, self.hash_secret(secret)):
            return None

        now = self._now()
        if not key.is_active_at(now):
            return None
        await self._repository.touch_last_used(
            key.key_id,
            used_at=now,
            used_ip=used_ip,
            older_than=now.subtract(seconds=LAST_USED_WRITE_INTERVAL_SECONDS),
        )
        return key

    def hash_secret(self, secret: str) -> bytes:
        """Return the keyed digest stored for a credential secret."""
        return hmac.digest(self._pepper, secret.encode(), hashlib.sha256)

    def _urlsafe_token(self, size: int) -> str:
        return base64.urlsafe_b64encode(self._token_bytes(size)).rstrip(b"=").decode()

    @staticmethod
    def _parse_token(token: str) -> tuple[str, str] | None:
        prefix, separator, remainder = token.partition("_")
        key_id, second_separator, secret = remainder.partition("_")
        if prefix != API_KEY_PREFIX or not separator or not second_separator or not key_id or not secret:
            return None
        return key_id, secret

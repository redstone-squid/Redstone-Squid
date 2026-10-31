"""API-key issuance and authentication."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterable

from whenever import Instant

from squid.auth.application.ports import ApiKeyRepository
from squid.auth.domain import ApiKey, IssuedApiKey
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.core.i18n import tr
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import CATALOGUE, Pattern, Subject

API_KEY_PREFIX = "sq"
API_KEY_SECRET_BYTES = 32
LAST_USED_WRITE_INTERVAL_SECONDS = 60


def hash_api_key_secret(pepper: bytes, secret: str) -> bytes:
    """Return the digest stored for an API-key secret.

    Exported so nothing re-derives the construction by hand; test fixtures that
    seed `api_keys` rows call this rather than repeating the `hmac.digest` line.
    See `docs/credential-hashing.md` for why a keyed SHA-256 rather than a
    password KDF: the secret is 32 CSPRNG bytes, so there is no low-entropy
    input space for a work factor to protect, and a KDF here would be reachable
    per-request by anyone who has seen a key ID.
    """
    # codeql[py/weak-sensitive-data-hashing]
    return hmac.digest(pepper, secret.encode(), hashlib.sha256)  # 256-bit random secret, not a password


class ApiKeyService:
    """Issue and verify high-entropy service credentials."""

    def __init__(
        self,
        repository: ApiKeyRepository,
        pepper: str | bytes,
        *,
        permissions: PermissionService | None = None,
        now: Callable[[], Instant] = Instant.now,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._repository = repository
        self._permissions = permissions
        self._pepper = pepper.encode() if isinstance(pepper, str) else pepper
        if not self._pepper:
            msg = tr(t"API key pepper must not be empty.")
            raise InvalidStateError(msg)
        self._now = now
        self._token_bytes = token_bytes

    async def issue(
        self,
        *,
        label: str,
        scopes: Iterable[str | Pattern],
        owner_account_id: int | None = None,
        created_by_account_id: int | None = None,
        expires_at: Instant | None = None,
    ) -> IssuedApiKey:
        """Create a credential, returning its plaintext token exactly once.

        A key may never carry authority its owner does not hold: that is AWS's
        permissions-boundary rule, and enforcing it here as well as at request
        time means an over-broad key cannot be *created* and then quietly wait
        for its owner to be promoted.

        Raises `InvalidPatternError` for a malformed pattern. Parsing happens
        before the boundary check rather than inside it, because the boundary
        check is skipped on the CLI bootstrap path -- which is how
        `buildsubmission.raed` used to reach the database and match nothing.
        """
        requested = frozenset(pattern if isinstance(pattern, Pattern) else Pattern.parse(pattern) for pattern in scopes)
        await self._reject_beyond_owner_authority(requested, owner_account_id)
        key_id = self._urlsafe_token(12)
        secret = self._urlsafe_token(API_KEY_SECRET_BYTES)
        key = await self._repository.add(
            key_id=key_id,
            secret_hash=self.hash_secret(secret),
            label=label,
            scopes=requested,
            owner_account_id=owner_account_id,
            created_by_account_id=created_by_account_id,
            expires_at=expires_at,
        )
        return IssuedApiKey(key=key, token=f"{API_KEY_PREFIX}_{key_id}_{secret}")

    async def _reject_beyond_owner_authority(self, patterns: frozenset[Pattern], owner_account_id: int | None) -> None:
        """Refuse patterns reaching nodes the owner does not hold.

        Skipped when no permission service is wired in, which is the CLI
        bootstrap path that runs before any owner exists; and an ownerless key is
        bounded only by its own patterns, since there is nobody to intersect
        with.
        """
        if self._permissions is None or owner_account_id is None:
            return
        subject = Subject(account_id=owner_account_id)
        for pattern in patterns:
            reached = CATALOGUE.expand(pattern)
            held = await self._permissions.capabilities(subject, reached)
            if missing := sorted(reached - held):
                msg = f"{missing[0]} is outside your authority; you cannot grant what you do not hold."
                raise AuthorizationError(msg, public_context={"pattern": pattern.raw, "node": missing[0]})

    async def authenticate(self, token: str, *, used_ip: str | None = None) -> ApiKey | None:
        """Return the active key matching *token*, or ``None`` for invalid credentials.

        Revocation and expiry are checked only after the digest matches, so a
        revoked key and a wrong secret are indistinguishable to the caller.
        """
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
        return hash_api_key_secret(self._pepper, secret)

    def _urlsafe_token(self, size: int) -> str:
        return base64.urlsafe_b64encode(self._token_bytes(size)).rstrip(b"=").decode()

    @staticmethod
    def _parse_token(token: str) -> tuple[str, str] | None:
        prefix, separator, remainder = token.partition("_")
        key_id, second_separator, secret = remainder.partition("_")
        if prefix != API_KEY_PREFIX or not separator or not second_separator or not key_id or not secret:
            return None
        return key_id, secret

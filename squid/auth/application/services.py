"""API-key issuance and authentication."""

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable, Iterable

from whenever import Instant

from squid.auth.application.ports import ApiKeyUnitOfWork
from squid.auth.domain import IssuedApiKey
from squid.auth.models.entities import ApiKey
from squid.core.errors import AuthorizationError, InvalidStateError
from squid.core.i18n import tr
from squid.permissions.application.services import PermissionService
from squid.permissions.domain import CATALOGUE, Pattern, Subject
from squid.unit_of_work import UnitOfWorkFactory

API_KEY_PREFIX = "sq"
API_KEY_ID_BYTES = 12
API_KEY_ID_LENGTH = (API_KEY_ID_BYTES * 8 + 5) // 6
API_KEY_SECRET_BYTES = 32
LAST_USED_WRITE_INTERVAL_SECONDS = 60


def hash_api_key_secret(pepper: bytes, secret: str) -> bytes:
    """Return the HMAC-SHA256 digest stored for an API-key secret.

    Issued secrets contain 32 random bytes. The deployment pepper is the HMAC key
    and stays outside the database. Authentication hashes the presented secret
    and compares it with the stored digest; the digest itself is not a token.
    See `docs/credential-hashing.md` for the choice of digest over a password KDF.
    """
    # codeql[py/weak-sensitive-data-hashing]
    return hmac.digest(pepper, secret.encode(), hashlib.sha256)  # 256-bit random secret, not a password


class ApiKeyService:
    """Issue API keys for service clients and authenticate their bearer tokens.

    A token contains a public key ID for lookup and a random secret for proof.
    Only the secret's keyed digest is stored, so issuance is the one opportunity
    to retrieve the complete token. Successful authentication returns the stored
    key, including its owner and permission-pattern scopes.

    Scopes limit which operations the credential may perform. The API's
    caller_allows also checks the owner's current permissions on each request;
    authentication here establishes the credential, not permission for an action.
    """

    def __init__(
        self,
        pepper: str | bytes,
        *,
        uow_factory: UnitOfWorkFactory[ApiKeyUnitOfWork],
        permissions: PermissionService | None = None,
        now: Callable[[], Instant] = Instant.now,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._uow_factory = uow_factory
        self._permissions = permissions
        self._pepper = pepper.encode() if isinstance(pepper, str) else pepper
        if not self._pepper:
            msg = tr("API key pepper must not be empty.")
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
        """Create a credential and return its plaintext token once.

        All scope strings are parsed before persistence. When a permission service
        and owner are supplied, reject scopes that reach nodes the owner does not
        currently hold. Ownerless keys and services without permission checks skip
        that boundary check; their callers must authorize issuance.
        """
        async with self._uow_factory() as uow:
            requested = frozenset(
                pattern if isinstance(pattern, Pattern) else Pattern.parse(pattern) for pattern in scopes
            )
            await self._reject_beyond_owner_authority(uow, requested, owner_account_id)
            key_id = self._urlsafe_token(API_KEY_ID_BYTES)
            secret = self._urlsafe_token(API_KEY_SECRET_BYTES)
            key = await uow.api_keys.add(
                key_id=key_id,
                secret_hash=self.hash_secret(secret),
                label=label,
                scopes=requested,
                owner_account_id=owner_account_id,
                created_by_account_id=created_by_account_id,
                expires_at=expires_at,
            )
            result = IssuedApiKey(key=key, token=f"{API_KEY_PREFIX}_{key_id}_{secret}")
            await uow.commit()
            return result

    async def _reject_beyond_owner_authority(
        self, uow: ApiKeyUnitOfWork, patterns: frozenset[Pattern], owner_account_id: int | None
    ) -> None:
        """Reject scopes that reach permission nodes the owner does not hold.

        Skip the check for ownerless keys or when no permission service is supplied,
        such as during CLI bootstrap.
        """
        if self._permissions is None or owner_account_id is None:
            return
        subject = Subject(account_id=owner_account_id)
        requested_nodes = frozenset(node for pattern in patterns for node in CATALOGUE.expand(pattern))
        held = await self._permissions.capabilities_in(uow, subject, requested_nodes)
        for pattern in patterns:
            reached = CATALOGUE.expand(pattern)
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
        async with self._uow_factory() as uow:
            key = await uow.api_keys.get_by_key_id(key_id)
            if key is None or not hmac.compare_digest(key.secret_hash, self.hash_secret(secret)):
                return None

            now = self._now()
            if not key.is_active_at(now):
                return None
            await uow.api_keys.touch_last_used(
                key.key_id,
                used_at=now,
                used_ip=used_ip,
                older_than=now.subtract(seconds=LAST_USED_WRITE_INTERVAL_SECONDS),
            )
            await uow.commit()
            return key

    def hash_secret(self, secret: str) -> bytes:
        """Return the keyed digest stored for a credential secret."""
        return hash_api_key_secret(self._pepper, secret)

    def _urlsafe_token(self, size: int) -> str:
        return base64.urlsafe_b64encode(self._token_bytes(size)).rstrip(b"=").decode()

    @staticmethod
    def _parse_token(token: str) -> tuple[str, str] | None:
        prefix, separator, remainder = token.partition("_")
        # Base64url IDs may contain underscores, so only their fixed-width boundary separates the secret.
        key_id = remainder[:API_KEY_ID_LENGTH]
        second_separator = remainder[API_KEY_ID_LENGTH : API_KEY_ID_LENGTH + 1]
        secret = remainder[API_KEY_ID_LENGTH + 1 :]
        if prefix != API_KEY_PREFIX or not separator or second_separator != "_" or not secret:
            return None
        return key_id, secret

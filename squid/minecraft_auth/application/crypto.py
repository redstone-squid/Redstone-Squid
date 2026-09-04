"""Keyed secret handling and PKCE verification for Minecraft authorization."""

import base64
import hashlib
import hmac
import re
import secrets
from collections.abc import Callable
from enum import StrEnum
from uuid import UUID

from squid.core.errors import InvalidStateError
from squid.core.i18n import tr
from squid.minecraft_auth.errors import InvalidPkceError

INSTALLATION_TOKEN_PREFIX = "sqpi"
PLAYER_TOKEN_PREFIX = "sqpt"
SECRET_BYTES = 32
USER_CODE_BYTES = 10
MIN_INSTALLATION_SECRET_CHARS = 32
MAX_INSTALLATION_SECRET_CHARS = 512

_PKCE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
_PKCE_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


class SecretPurpose(StrEnum):
    """Domain-separation labels for values protected by one deployment pepper."""

    INSTALLATION = "installation"
    DEVICE_CODE = "device-code"
    USER_CODE = "user-code"
    PLAYER_TOKEN = "player-token"


class MinecraftSecretCodec:
    """Generate one-time values and persist only domain-separated HMAC digests."""

    def __init__(
        self,
        pepper: str | bytes,
        *,
        token_bytes: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        self._pepper = pepper.encode() if isinstance(pepper, str) else pepper
        if not self._pepper:
            msg = tr(t"Minecraft authorization pepper must not be empty.")
            raise InvalidStateError(msg)
        self._token_bytes = token_bytes

    def random_secret(self) -> str:
        """Return a URL-safe 256-bit bearer secret."""
        return self._urlsafe(self._token_bytes(SECRET_BYTES))

    def random_user_code(self) -> str:
        """Return an 80-bit human-transcribable approval code."""
        compact = base64.b32encode(self._token_bytes(USER_CODE_BYTES)).decode().rstrip("=")
        return "-".join(compact[index : index + 4] for index in range(0, len(compact), 4))

    def digest(self, purpose: SecretPurpose, value: str) -> bytes:
        """Return a keyed digest suitable for persistence and comparison.

        See `docs/credential-hashing.md`: every value reaching here is CSPRNG
        output (256-bit secrets, an 80-bit user code), so keyed SHA-256 is the
        right primitive and a password KDF defends entropy that is not at risk.
        """
        payload = purpose.value.encode() + b"\0" + value.encode()
        # codeql[py/weak-sensitive-data-hashing]
        return hmac.digest(self._pepper, payload, hashlib.sha256)  # high-entropy random value, not a password

    def installation_token(self, installation_id: UUID, secret: str) -> str:
        """Compose a self-identifying Paper credential."""
        return f"{INSTALLATION_TOKEN_PREFIX}_{installation_id.hex}_{secret}"

    def player_token(self, grant_id: UUID, secret: str) -> str:
        """Compose a self-identifying short-lived player credential."""
        return f"{PLAYER_TOKEN_PREFIX}_{grant_id.hex}_{secret}"

    @staticmethod
    def parse_installation_token(token: str) -> tuple[UUID, str] | None:
        """Parse a Paper credential without authenticating its secret."""
        return _parse_token(token, INSTALLATION_TOKEN_PREFIX)

    @staticmethod
    def parse_player_token(token: str) -> tuple[UUID, str] | None:
        """Parse a player credential without authenticating its secret."""
        return _parse_token(token, PLAYER_TOKEN_PREFIX)

    @staticmethod
    def normalize_user_code(code: str) -> str:
        """Return the canonical approval-code representation."""
        return code.strip().replace("-", "").upper()

    @staticmethod
    def validate_s256_challenge(challenge: str) -> str:
        """Validate and return an RFC 7636 S256 code challenge."""
        if _PKCE_CHALLENGE.fullmatch(challenge) is None:
            raise InvalidPkceError
        return challenge

    @staticmethod
    def verify_s256(challenge: str, verifier: str) -> bool:
        """Return whether an RFC 7636 verifier proves possession of *challenge*."""
        if _PKCE_VERIFIER.fullmatch(verifier) is None:
            return False
        actual = MinecraftSecretCodec._urlsafe(hashlib.sha256(verifier.encode("ascii")).digest())
        return hmac.compare_digest(actual, challenge)

    @staticmethod
    def _urlsafe(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _parse_token(token: str, expected_prefix: str) -> tuple[UUID, str] | None:
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != expected_prefix or not parts[2]:
        return None
    try:
        identifier = UUID(hex=parts[1])
    except ValueError:
        return None
    return identifier, parts[2]

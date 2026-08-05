"""Opaque signed cursors for transport-neutral keyset pagination."""

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import NoReturn, cast

from squid.core.errors import ErrorCode, JSONValue, ValidationError


class InvalidPageCursorError(ValidationError):
    """A collection cursor is malformed, tampered with, or reused elsewhere."""

    default_code = ErrorCode.INVALID_CURSOR


class SignedCursor:
    """Sign arbitrary JSON cursor payloads and bind them to one collection view."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            msg = "cursor secret must contain at least 16 bytes"
            raise ValueError(msg)
        self._secret = secret

    def encode(self, payload: Mapping[str, JSONValue], *, binding: str) -> str:
        """Return an opaque URL-safe token for a payload and collection binding."""
        envelope = {"binding": _binding_hash(binding), "payload": payload}
        encoded = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.digest(self._secret, encoded, "sha256")
        return base64.urlsafe_b64encode(encoded + signature).rstrip(b"=").decode("ascii")

    def decode(self, cursor: str, *, binding: str) -> dict[str, JSONValue]:
        """Verify and decode a cursor for the expected collection binding."""
        try:
            padding = "=" * (-len(cursor) % 4)
            signed = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        except (ValueError, UnicodeError) as error:
            _invalid("cursor is not valid URL-safe base64", error)
        digest_size = hashlib.sha256().digest_size
        if len(signed) <= digest_size:
            _invalid("cursor is truncated")
        encoded, signature = signed[:-digest_size], signed[-digest_size:]
        if not hmac.compare_digest(signature, hmac.digest(self._secret, encoded, "sha256")):
            _invalid("cursor signature is invalid")
        try:
            decoded: object = json.loads(encoded)
            if not isinstance(decoded, dict):
                _invalid("cursor payload is invalid")
            envelope = cast(dict[str, object], decoded)
            if envelope.get("binding") != _binding_hash(binding):
                _invalid("cursor belongs to a different collection")
            payload = envelope["payload"]
            if not isinstance(payload, dict):
                _invalid("cursor payload is invalid")
            return cast(dict[str, JSONValue], payload)
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            _invalid("cursor payload is invalid", error)


def _binding_hash(binding: str) -> str:
    return hashlib.sha256(binding.encode()).hexdigest()


def _invalid(message: str, cause: Exception | None = None) -> NoReturn:
    raise InvalidPageCursorError(message) from cause

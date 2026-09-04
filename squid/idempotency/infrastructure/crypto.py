"""Authenticated encryption for durable idempotency response bodies."""

import json
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from squid.core.errors import ConfigurationError, DataIntegrityError
from squid.core.i18n import tr
from squid.idempotency.domain import UnsafeHttpMethod

_NONCE_BYTES = 12
_AAD_DOMAIN = b"redstone-squid:idempotency-response:v1"


class IdempotencyCiphertextError(DataIntegrityError):
    """A retained response could not be authenticated or decrypted."""


class IdempotencyEncryptionUnavailableError(ConfigurationError):
    """Response encryption was requested without an API keyring."""


@dataclass(frozen=True, slots=True)
class ResponseEncryptionMetadata:
    """Stable record and response fields bound to one ciphertext."""

    request_id: UUID
    caller: str
    idempotency_key: str
    request_fingerprint: bytes
    method: UnsafeHttpMethod
    route: str
    status_code: int
    headers: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class EncryptedResponseBody:
    """Database-ready authenticated response body fields."""

    key_id: str
    nonce: bytes
    ciphertext: bytes


class IdempotencyResponseCipher:
    """Encrypt with the active key and decrypt with any retained rotation key."""

    def __init__(self, *, active_key_id: str, keys: Mapping[str, bytes]) -> None:
        if active_key_id not in keys:
            msg = tr(t"The active idempotency encryption key is absent from the keyring.")
            raise IdempotencyEncryptionUnavailableError(msg)
        if any(len(key) != 32 for key in keys.values()):
            msg = tr(t"Idempotency encryption keys must be exactly 32 bytes.")
            raise IdempotencyEncryptionUnavailableError(msg)
        self._active_key_id = active_key_id
        self._keys = dict(keys)

    def encrypt(self, body: bytes, metadata: ResponseEncryptionMetadata) -> EncryptedResponseBody:
        """Seal one body and authenticate its owning request and response metadata."""
        nonce = secrets.token_bytes(_NONCE_BYTES)
        key = self._keys[self._active_key_id]
        ciphertext = AESGCM(key).encrypt(nonce, body, _associated_data(metadata, self._active_key_id))
        return EncryptedResponseBody(self._active_key_id, nonce, ciphertext)

    def decrypt(
        self,
        encrypted: EncryptedResponseBody,
        metadata: ResponseEncryptionMetadata,
    ) -> bytes:
        """Authenticate and open one body, failing closed on unknown or altered data."""
        key = self._keys.get(encrypted.key_id)
        if key is None:
            msg = "The idempotency response uses an unavailable encryption key."
            raise IdempotencyCiphertextError(msg)
        if len(encrypted.nonce) != _NONCE_BYTES:
            msg = "The idempotency response has an invalid encryption nonce."
            raise IdempotencyCiphertextError(msg)
        try:
            return AESGCM(key).decrypt(
                encrypted.nonce,
                encrypted.ciphertext,
                _associated_data(metadata, encrypted.key_id),
            )
        except InvalidTag as exc:
            msg = "The idempotency response ciphertext failed authentication."
            raise IdempotencyCiphertextError(msg) from exc


def _associated_data(metadata: ResponseEncryptionMetadata, key_id: str) -> bytes:
    canonical_headers = json.dumps(
        dict(metadata.headers),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    fields = (
        _AAD_DOMAIN,
        key_id.encode(),
        metadata.request_id.bytes,
        metadata.caller.encode(),
        metadata.idempotency_key.encode(),
        metadata.request_fingerprint,
        metadata.method.encode(),
        metadata.route.encode(),
        metadata.status_code.to_bytes(4, "big", signed=False),
        canonical_headers,
    )
    return b"".join(len(field).to_bytes(8, "big") + field for field in fields)

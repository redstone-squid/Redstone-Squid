"""Opaque, signed cursor encoding for stable search pagination."""

import base64
import hashlib
import hmac
import json
import math
from dataclasses import asdict
from typing import Literal, NoReturn, cast

from squid.search.domain.models import CursorPosition, SearchMode, SearchRequest, SearchScope


class InvalidCursorError(ValueError):
    """Raised when a cursor is malformed, tampered with, or belongs to another request."""


class CursorCodec:
    """Encode and validate search positions with an application secret."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 16:
            msg = "cursor secret must contain at least 16 bytes"
            raise ValueError(msg)
        self._secret = secret

    def encode(self, position: CursorPosition) -> str:
        """Encode a stable position as an opaque URL-safe token."""
        payload = json.dumps(asdict(position), separators=(",", ":"), sort_keys=True).encode()
        signature = hmac.digest(self._secret, payload, "sha256")
        return _encode(payload + signature)

    def decode(self, cursor: str, *, request: SearchRequest | None = None) -> CursorPosition:
        """Decode a token and optionally ensure that it belongs to a request."""
        try:
            signed = _decode(cursor)
        except (ValueError, UnicodeError) as error:
            _invalid("cursor is not valid URL-safe base64", cause=error)
        if len(signed) <= hashlib.sha256().digest_size:
            _invalid("cursor is truncated")
        payload = signed[: -hashlib.sha256().digest_size]
        signature = signed[-hashlib.sha256().digest_size :]
        if not hmac.compare_digest(signature, hmac.digest(self._secret, payload, "sha256")):
            _invalid("cursor signature is invalid")
        try:
            position = _parse_payload(payload)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            _invalid("cursor payload is invalid", cause=error)
        if request is not None:
            expected_hash = self.request_hash(request)
            if (
                position.query_hash != expected_hash
                or position.scope is not request.scope
                or position.mode is not request.mode
            ):
                _invalid("cursor belongs to a different search request")
        return position

    @staticmethod
    def request_hash(request: SearchRequest) -> str:
        """Hash the request properties which determine result ordering."""
        stable = json.dumps(
            {
                "mode": request.mode.value,
                "query": " ".join(request.query.split()),
                "scope": request.scope.value,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        return hashlib.sha256(stable).hexdigest()


def _required_string(payload: dict[str, object], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise TypeError
    return value


def _resource_kind(payload: dict[str, object]) -> Literal["record", "build", "metadata"]:
    value = _required_string(payload, "resource_kind")
    if value not in {"record", "build", "metadata"}:
        raise TypeError
    return cast(Literal["record", "build", "metadata"], value)


def _parse_payload(payload: bytes) -> CursorPosition:
    decoded: object = json.loads(payload)
    if not isinstance(decoded, dict):
        raise TypeError
    raw = cast(dict[str, object], decoded)
    score = raw["score"]
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, int | float) or not math.isfinite(score)
    ):
        raise TypeError
    return CursorPosition(
        query_hash=_required_string(raw, "query_hash"),
        scope=SearchScope(_required_string(raw, "scope")),
        mode=SearchMode(_required_string(raw, "mode")),
        score=float(score) if score is not None else None,
        resource_kind=_resource_kind(raw),
        source_id=_required_string(raw, "source_id"),
    )


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def _invalid(message: str, *, cause: Exception | None = None) -> NoReturn:
    raise InvalidCursorError(message) from cause

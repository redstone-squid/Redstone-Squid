"""Transport-neutral envelopes for committed replicated updates."""

import base64
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

_MAX_ENVELOPE_BYTES = 1_500_000


@dataclass(frozen=True, slots=True)
class ReplicationUpdate:
    """One committed backend update with its routing fields, as a JSON envelope of at most 1 500 000 bytes.

    The envelope carries a SHA-256 of `payload` to catch corruption; it is not signed, so
    authenticating the sender is the transport's job. The payload is never interpreted here.
    """

    document_id: str
    backend_id: str
    source_replica_id: str
    """The `replica_id` that committed the update."""
    update_id: uuid.UUID
    """Minted by `create`; `ReplicatedDocument.import_update` skips an id it has already seen."""
    payload: bytes
    origin_action_id: uuid.UUID | None = None
    """The local action that committed the payload; `None` for an `export_since` export."""
    schema: int = 1
    """Wire schema; `decode` accepts 1 only."""

    @classmethod
    def create(
        cls,
        *,
        document_id: str,
        backend_id: str,
        source_replica_id: str,
        payload: bytes,
        origin_action_id: uuid.UUID | None,
    ) -> ReplicationUpdate:
        """Mint a fresh UUIDv7 `update_id`."""
        return cls(document_id, backend_id, source_replica_id, uuid.uuid7(), payload, origin_action_id)

    def encode(self) -> bytes:
        """Compact sorted-key JSON with a base64 payload; raises `ValueError` past 1 500 000 bytes."""
        body = {
            "backend": self.backend_id,
            "document": self.document_id,
            "hash": hashlib.sha256(self.payload).hexdigest(),
            "origin_action": None if self.origin_action_id is None else str(self.origin_action_id),
            "payload": base64.b64encode(self.payload).decode("ascii"),
            "schema": self.schema,
            "source_replica": self.source_replica_id,
            "update_id": str(self.update_id),
        }
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > _MAX_ENVELOPE_BYTES:
            message = "replicated envelope exceeds the maximum encoded size"
            raise ValueError(message)
        return encoded

    @classmethod
    def decode(cls, encoded: bytes) -> ReplicationUpdate:
        """Parse untrusted bytes; raises `ValueError` for anything that is not a well-formed envelope.

        That covers an oversize input, invalid JSON, a schema other than 1, a missing or
        non-string routing field, bad base64 or UUIDs, and a payload hash mismatch.
        """
        if len(encoded) > _MAX_ENVELOPE_BYTES:
            message = "replicated envelope exceeds the maximum encoded size"
            raise ValueError(message)
        try:
            body: Any = json.loads(encoded)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "replicated envelope is not valid JSON"
            raise ValueError(message) from error
        if not isinstance(body, dict) or body.get("schema") != 1:
            message = "replicated envelope has an unsupported schema"
            raise ValueError(message)
        required = ("backend", "document", "source_replica", "update_id", "payload", "hash")
        if any(not isinstance(body.get(name), str) for name in required):
            message = "replicated envelope has invalid routing fields"
            raise ValueError(message)
        try:
            payload = base64.b64decode(body["payload"], validate=True)
            update_id = uuid.UUID(body["update_id"])
            origin = None if body.get("origin_action") is None else uuid.UUID(body["origin_action"])
        except (ValueError, TypeError) as error:
            message = "replicated envelope has invalid encoded identifiers or payload"
            raise ValueError(message) from error
        if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), body["hash"]):
            message = "replicated envelope payload hash does not match"
            raise ValueError(message)
        return cls(body["document"], body["backend"], body["source_replica"], update_id, payload, origin)


__all__ = ["ReplicationUpdate"]

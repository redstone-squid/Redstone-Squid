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
    """A routed, integrity-checked backend update safe to hand to an application transport."""

    document_id: str
    backend_id: str
    source_replica_id: str
    update_id: uuid.UUID
    payload: bytes
    origin_action_id: uuid.UUID | None = None
    schema: int = 1

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
        return cls(document_id, backend_id, source_replica_id, uuid.uuid7(), payload, origin_action_id)

    def encode(self) -> bytes:
        """Encode the envelope without interpreting the backend payload."""
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
        """Decode and verify an untrusted envelope before it reaches the commit gate."""
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

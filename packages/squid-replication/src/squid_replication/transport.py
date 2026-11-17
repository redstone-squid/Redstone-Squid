"""Transport-neutral envelopes for committed replicated updates."""

import base64
import binascii
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

_MAX_ENVELOPE_BYTES = 1_500_000


class _UpdateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str
    document: str
    hash: str
    origin_action: uuid.UUID | None
    payload: str
    version: Literal[1] = Field(default=1, alias="schema")
    source_replica: str
    update_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class ReplicationUpdate:
    """Routing metadata and backend bytes for one transport update.

    Encoding includes a SHA-256 payload checksum; decoding checks it and validates the
    envelope schema and identifiers. The checksum detects corruption, not sender
    identity. The host transport supplies authentication and authorization.
    """

    document_id: str
    backend_id: str
    source_replica_id: str
    update_id: uuid.UUID
    payload: bytes
    origin_transaction_id: uuid.UUID | None = None
    schema: int = 1

    @classmethod
    def create(
        cls,
        *,
        document_id: str,
        backend_id: str,
        source_replica_id: str,
        payload: bytes,
        origin_transaction_id: uuid.UUID | None,
    ) -> ReplicationUpdate:
        return cls(document_id, backend_id, source_replica_id, uuid.uuid7(), payload, origin_transaction_id)

    def encode(self) -> bytes:
        """Encode the envelope without interpreting the backend payload."""
        if self.schema != 1:
            message = "replicated envelope has an unsupported schema"
            raise ValueError(message)
        envelope = _UpdateEnvelope(
            backend=self.backend_id,
            document=self.document_id,
            hash=hashlib.sha256(self.payload).hexdigest(),
            origin_action=self.origin_transaction_id,
            payload=base64.b64encode(self.payload).decode("ascii"),
            source_replica=self.source_replica_id,
            update_id=self.update_id,
        )
        encoded = json.dumps(
            envelope.model_dump(mode="json", by_alias=True), sort_keys=True, separators=(",", ":")
        ).encode()
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
            envelope = _UpdateEnvelope.model_validate_json(encoded, strict=True)
        except ValidationError as error:
            try:
                body = json.loads(encoded)
            except UnicodeDecodeError, json.JSONDecodeError:
                message = "replicated envelope is not valid JSON"
                raise ValueError(message) from error
            if not isinstance(body, dict) or body.get("schema") != 1:
                message = "replicated envelope has an unsupported schema"
            elif any(item["loc"] and item["loc"][0] in ("update_id", "origin_action") for item in error.errors()):
                message = "replicated envelope has invalid encoded identifiers or payload"
            else:
                message = "replicated envelope has invalid routing fields"
            raise ValueError(message) from error
        try:
            payload = base64.b64decode(envelope.payload, validate=True)
        except (binascii.Error, ValueError, TypeError) as error:
            message = "replicated envelope has invalid encoded identifiers or payload"
            raise ValueError(message) from error
        if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), envelope.hash):
            message = "replicated envelope payload hash does not match"
            raise ValueError(message)
        return cls(
            envelope.document,
            envelope.backend,
            envelope.source_replica,
            envelope.update_id,
            payload,
            envelope.origin_action,
        )


__all__ = ["ReplicationUpdate"]

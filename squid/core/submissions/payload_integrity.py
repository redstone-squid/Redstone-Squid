"""Canonical integrity helpers for durable submission payloads."""

import hashlib
import json
from collections.abc import Mapping


def submission_payload_digest(payload: Mapping[str, object]) -> str:
    """Hash the canonical JSON representation persisted beside a finalization payload."""
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()

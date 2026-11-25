"""Retention and authority boundaries shared by HTTP and application replay."""

import hashlib
import json

from squid.core.actors import ActingContext

REPLAY_RETENTION_HOURS = 24


def replay_fingerprint(content: bytes, context: ActingContext) -> bytes:
    """Bind restricted credentials and impersonation sessions to their original operation."""
    scopes = context.effective.credential_scopes
    if scopes is None and context.session_id is None:
        return content
    authority = json.dumps(
        [
            context.operator.reference,
            context.operator.credential_id,
            context.effective.credential_id,
            str(context.session_id),
            sorted(scopes) if scopes is not None else None,
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(content + authority.encode()).digest()

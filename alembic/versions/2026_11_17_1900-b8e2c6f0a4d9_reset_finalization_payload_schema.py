"""Consolidate retained finalization payloads into the current schema.

Revision ID: b8e2c6f0a4d9
Revises: a7c1e5b9d304
"""

import copy
import hashlib
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b8e2c6f0a4d9"
down_revision = "a7c1e5b9d304"
branch_labels = None
depends_on = None


def _rewrite(payload: dict) -> dict:
    version = payload.get("payload_schema")
    if version == 1:
        return payload
    if type(version) is not int or version not in range(2, 9):
        message = "Cannot reset an unknown finalization payload schema"
        raise RuntimeError(message)
    document = copy.deepcopy(payload)
    creators = document.pop("creators", [])
    if not document.get("credits"):
        document["credits"] = [
            {
                "kind": "name",
                "name": name,
                "username": None,
                "subject": None,
                "role": "creator",
                "attribution": "legacy",
                "source_message_id": None,
            }
            for name in dict.fromkeys(creators)
            if name.strip()
        ]
    document.setdefault("links", [])
    document.setdefault("source_installation_id", None)
    document.setdefault("sponsor", None)
    document["taxonomy"]["restriction_proposal_fields"] = document.pop("restriction_proposal_fields", [])
    details = document["details"]
    if details["kind"] == "door":
        details.setdefault("expansion", None)
    if details["kind"] == "general":
        details["pattern_keys"] = document.pop("general_pattern_keys", [])
        details["pattern_proposals"] = document.pop("general_pattern_proposals", [])
    document["payload_schema"] = 1
    return document


def _digest(payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    for name, identity in [("submission_finalization_jobs", "id"), ("submission_finalization_inputs", "job_id")]:
        table = sa.table(
            name, sa.column(identity, sa.Uuid()), sa.column("payload", JSONB()), sa.column("payload_sha256", sa.Text())
        )
        rows = connection.execute(
            sa.select(table.c[identity], table.c.payload, table.c.payload_sha256).where(table.c.payload.is_not(None))
        ).all()
        for row_id, payload, digest in rows:
            if _digest(payload) != digest:
                message = "Cannot rewrite a finalization payload with a conflicting digest"
                raise RuntimeError(message)
            rewritten = _rewrite(payload)
            connection.execute(
                table.update()
                .where(table.c[identity] == row_id)
                .values(payload=rewritten, payload_sha256=_digest(rewritten))
            )


def downgrade() -> None:
    message = "The retired finalization schemas cannot be reconstructed; restore a database backup"
    raise RuntimeError(message)

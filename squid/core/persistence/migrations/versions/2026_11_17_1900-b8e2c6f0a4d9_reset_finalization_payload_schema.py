"""Consolidate retained finalization payloads into the current schema.

Revision ID: b8e2c6f0a4d9
Revises: a7c1e5b9d304
"""

import copy
import hashlib
import json
from collections.abc import Callable

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


def _migrate_payloads(rewrite: Callable[[dict], dict]) -> None:
    connection = op.get_bind()
    # Rewrite the retained representation under the migration transaction's table
    # lock. A failure rolls back both the payload changes and the trigger state.
    op.execute("ALTER TABLE submission_finalization_inputs DISABLE TRIGGER submission_finalization_inputs_immutable")
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
            rewritten = rewrite(payload)
            connection.execute(
                table.update()
                .where(table.c[identity] == row_id)
                .values(payload=rewritten, payload_sha256=_digest(rewritten))
            )
    op.execute("ALTER TABLE submission_finalization_inputs ENABLE TRIGGER submission_finalization_inputs_immutable")


def upgrade() -> None:
    _migrate_payloads(_rewrite)


def _restore(payload: dict) -> dict:
    if payload.get("payload_schema") != 1:
        message = "Cannot restore an unknown finalization payload schema"
        raise RuntimeError(message)
    document = copy.deepcopy(payload)
    document["creators"] = []
    document["restriction_proposal_fields"] = document["taxonomy"].pop("restriction_proposal_fields")
    details = document["details"]
    if details["kind"] == "general":
        document["payload_schema"] = 8
        document["general_pattern_keys"] = details.pop("pattern_keys")
        document["general_pattern_proposals"] = details.pop("pattern_proposals")
    else:
        document["payload_schema"] = 7
    return document


def downgrade() -> None:
    # Restore the most recent retired representation, without discarding facts
    # or reintroducing compatibility branches in the application's codec.
    _migrate_payloads(_restore)

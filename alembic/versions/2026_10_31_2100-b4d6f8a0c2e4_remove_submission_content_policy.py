"""Remove submission content filters and migrate stored artifact references.

Revision ID: b4d6f8a0c2e4
Revises: a3c5e7f9b1d2
Create Date: 2026-10-31 21:00:00.000000
"""

import hashlib
import json

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "b4d6f8a0c2e4"
down_revision: str = "a3c5e7f9b1d2"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    _migrate_payloads(forward=True)
    _migrate_drafts(forward=True)


def downgrade() -> None:
    _migrate_payloads(forward=False)
    _migrate_drafts(forward=False)


def _migrate_drafts(*, forward: bool) -> None:
    old_revision, new_revision = (1, 2) if forward else (2, 1)
    # Invalidate stale client edits while keeping job revision fences aligned.
    op.execute(
        sa.text(
            "UPDATE submission_finalization_jobs SET draft_revision = draft_revision + 1 "
            "WHERE draft_id IN (SELECT id FROM submission_drafts "
            f"WHERE schema_id = 'build_submission.v1' AND schema_revision = {old_revision})"
        )
    )
    answers = (
        "answers - 'include_inventories' - 'include_free_text'"
        if forward
        else 'answers || \'{"include_inventories": true, "include_free_text": true}\'::jsonb'
    )
    op.execute(
        sa.text(
            f"UPDATE submission_drafts SET schema_revision = {new_revision}, revision = revision + 1, "
            f"answers = {answers}, updated_at = now() "
            f"WHERE schema_id = 'build_submission.v1' AND schema_revision = {old_revision}"
        )
    )


def _migrate_payloads(*, forward: bool) -> None:
    jobs = sa.table(
        "submission_finalization_jobs",
        sa.column("id", sa.Uuid()),
        sa.column("payload", JSONB()),
        sa.column("payload_sha256", sa.Text()),
    )
    old_schema, new_schema = (1, 2) if forward else (2, 1)
    connection = op.get_bind()
    rows = connection.execute(sa.select(jobs.c.id, jobs.c.payload).where(jobs.c.payload.is_not(None)))
    for job_id, payload in rows:
        if payload.get("payload_schema") != old_schema:
            continue
        policy = payload["schematic_policy"]
        artifacts = payload["artifacts"]
        if forward:
            policy.pop("include_inventories", None)
            policy.pop("include_free_text", None)
            artifacts["schematic_id"] = artifacts.pop("sanitized_schematic_id")
        else:
            # A stored upload is not evidence that the retired sanitizer ran.
            if artifacts["schematic_id"] is not None:
                msg = "Cannot restore the sanitizer contract for jobs with stored schematic artifacts."
                raise RuntimeError(msg)
            artifacts["sanitized_schematic_id"] = artifacts.pop("schematic_id")
            policy.update(include_inventories=True, include_free_text=True)
        payload["payload_schema"] = new_schema
        if payload["schema_id"] == "build_submission.v1" and payload["schema_revision"] == old_schema:
            payload["schema_revision"] = new_schema
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        connection.execute(
            jobs.update()
            .where(jobs.c.id == job_id)
            .values(payload=payload, payload_sha256=hashlib.sha256(encoded).hexdigest())
        )

"""Snapshot Paper sponsor attribution.

Revision ID: a5b6c7d8e9f0
Revises: f4a5b6c7d8e9
Create Date: 2026-08-11 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a5b6c7d8e9f0"
down_revision: str | Sequence[str] | None = "f4a5b6c7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Retain trusted Paper provenance and immutable public sponsor snapshots."""
    op.execute("LOCK TABLE submission_finalization_jobs IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM submission_finalization_jobs
                WHERE payload ->> 'payload_schema' = '1'
                  AND payload ->> 'sponsor_attribution' = 'true'
            ) THEN
                RAISE EXCEPTION 'cannot migrate unresolved legacy sponsor attribution requests';
            END IF;
        END;
        $$
        """
    )
    op.create_check_constraint(
        "submission_finalization_jobs_legacy_sponsor_forbidden",
        "submission_finalization_jobs",
        "payload IS NULL OR NOT (payload ->> 'payload_schema' = '1' AND payload ->> 'sponsor_attribution' = 'true')",
    )
    op.add_column(
        "submission_drafts",
        sa.Column("source_installation_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "submission_drafts_installation_requires_paper",
        "submission_drafts",
        "source_installation_id IS NULL OR origin = 'paper'",
    )
    op.create_index(
        "submission_drafts_source_installation_idx",
        "submission_drafts",
        ["source_installation_id"],
        unique=False,
        postgresql_where=sa.text("source_installation_id IS NOT NULL"),
    )

    op.add_column("builds", sa.Column("sponsor_installation_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("builds", sa.Column("sponsor_display_name", sa.Text(), nullable=True))
    op.add_column("builds", sa.Column("sponsor_address", sa.Text(), nullable=True))
    op.add_column("builds", sa.Column("sponsor_description", sa.Text(), nullable=True))
    op.add_column("builds", sa.Column("sponsor_website_url", sa.Text(), nullable=True))
    op.create_check_constraint(
        "builds_sponsor_projection_complete",
        "builds",
        "sponsor_installation_id IS NOT NULL OR "
        "(sponsor_display_name IS NULL AND sponsor_address IS NULL AND sponsor_description IS NULL "
        "AND sponsor_website_url IS NULL)",
    )
    op.create_check_constraint(
        "builds_sponsor_display_name_length",
        "builds",
        "sponsor_display_name IS NULL OR char_length(sponsor_display_name) BETWEEN 1 AND 80",
    )
    op.create_check_constraint(
        "builds_sponsor_address_length",
        "builds",
        "sponsor_address IS NULL OR char_length(sponsor_address) BETWEEN 1 AND 255",
    )
    op.create_check_constraint(
        "builds_sponsor_description_length",
        "builds",
        "sponsor_description IS NULL OR char_length(sponsor_description) BETWEEN 1 AND 500",
    )
    op.create_check_constraint(
        "builds_sponsor_website_valid",
        "builds",
        "sponsor_website_url IS NULL OR (char_length(sponsor_website_url) BETWEEN 1 AND 2048 "
        "AND sponsor_website_url ~ '^https?://')",
    )


def downgrade() -> None:
    """Remove sponsor columns only when doing so cannot discard attribution."""
    op.execute("LOCK TABLE submission_finalization_jobs, submission_drafts, builds IN ACCESS EXCLUSIVE MODE")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM builds
                WHERE sponsor_installation_id IS NOT NULL
                   OR sponsor_display_name IS NOT NULL
                   OR sponsor_address IS NOT NULL
                   OR sponsor_description IS NOT NULL
                   OR sponsor_website_url IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM submission_drafts WHERE source_installation_id IS NOT NULL
            ) OR EXISTS (
                SELECT 1 FROM submission_finalization_jobs
                WHERE payload ->> 'payload_schema' = '2'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade while sponsor attribution schema data is retained';
            END IF;
        END;
        $$
        """
    )
    op.drop_constraint(
        "submission_finalization_jobs_legacy_sponsor_forbidden",
        "submission_finalization_jobs",
        type_="check",
    )
    op.drop_constraint("builds_sponsor_website_valid", "builds", type_="check")
    op.drop_constraint("builds_sponsor_description_length", "builds", type_="check")
    op.drop_constraint("builds_sponsor_address_length", "builds", type_="check")
    op.drop_constraint("builds_sponsor_display_name_length", "builds", type_="check")
    op.drop_constraint("builds_sponsor_projection_complete", "builds", type_="check")
    op.drop_column("builds", "sponsor_website_url")
    op.drop_column("builds", "sponsor_description")
    op.drop_column("builds", "sponsor_address")
    op.drop_column("builds", "sponsor_display_name")
    op.drop_column("builds", "sponsor_installation_id")

    op.drop_index("submission_drafts_source_installation_idx", table_name="submission_drafts")
    op.drop_constraint("submission_drafts_installation_requires_paper", "submission_drafts", type_="check")
    op.drop_column("submission_drafts", "source_installation_id")

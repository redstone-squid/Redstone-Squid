"""Preserve upload audio instead of recording an audio-removal option."""

import sqlalchemy as sa

from alembic import op

revision: str = "a7c1e5b9d304"
down_revision: str | None = "f6bad4e8a273"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("media_uploads_audio_only_for_video", "media_uploads", type_="check")
    op.drop_column("media_uploads", "strip_audio")
    op.create_table_comment(
        "media_normalization_jobs",
        "A processing request retained after completion, failure, or discard for cleanup and retries.",
    )
    op.create_table_comment(
        "media_draft_references", "One independently discardable draft reference to a shared processing job."
    )


def downgrade() -> None:
    op.create_table_comment(
        "media_normalization_jobs",
        "A normalization request retained after completion, failure, or discard for cleanup and retries.",
    )
    op.create_table_comment(
        "media_draft_references", "One independently discardable draft reference to a shared normalization job."
    )
    op.add_column("media_uploads", sa.Column("strip_audio", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_check_constraint(
        "media_uploads_audio_only_for_video", "media_uploads", "kind = 'video' OR NOT strip_audio"
    )

"""Retain a build edit draft's source revision and original answers."""

from alembic import op

revision = "c5a123d79e04"
down_revision = "9a3c0b18f4ed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE build_vote_sessions ADD COLUMN public_suggestion boolean NOT NULL DEFAULT false")
    op.execute("ALTER TABLE submission_drafts ADD COLUMN edit_source jsonb")


def downgrade() -> None:
    op.execute("ALTER TABLE build_vote_sessions DROP COLUMN public_suggestion")
    op.execute("ALTER TABLE submission_drafts DROP COLUMN edit_source")

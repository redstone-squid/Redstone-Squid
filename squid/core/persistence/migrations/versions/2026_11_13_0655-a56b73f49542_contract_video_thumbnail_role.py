"""Retire the legacy poster artifact role.

Stop media workers and readers before upgrading, and deploy the new code before
restarting them. Report payloads and content-addressed object keys stay unchanged.
Downgrade before restarting older binaries; it restores poster roles for them.

Revision ID: a56b73f49542
Revises: f45a62f38431
"""

from alembic import op

revision: str = "a56b73f49542"
down_revision: str = "f45a62f38431"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("UPDATE media_artifacts SET role = 'video_thumbnail' WHERE role = 'poster'")
    op.drop_constraint("media_artifacts_role_check", "media_artifacts", type_="check")
    op.create_check_constraint(
        "media_artifacts_role_check", "media_artifacts", "role IN ('output', 'video_thumbnail', 'report')"
    )


def downgrade() -> None:
    op.drop_constraint("media_artifacts_role_check", "media_artifacts", type_="check")
    op.create_check_constraint(
        "media_artifacts_role_check", "media_artifacts", "role IN ('output', 'poster', 'video_thumbnail', 'report')"
    )
    op.execute("UPDATE media_artifacts SET role = 'poster' WHERE role = 'video_thumbnail'")

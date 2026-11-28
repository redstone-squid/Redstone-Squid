"""Retain submitter-visible feedback on a moderation decision."""

from alembic import op

revision = "1af9428ed50c"
down_revision = "6f41c27d3b9a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE build_submissions ADD COLUMN feedback text")
    op.create_table_comment(
        "build_submissions", "A submitted version, retained independently of its draft and voting session."
    )
    op.create_table_comment(
        "build_image_mirrors", "A bounded copy of an image retained for the lifetime of its source link."
    )
    op.execute("""UPDATE build_submissions s SET decided_revision = outcome.revision
        FROM (
            SELECT DISTINCT ON (h.build_id) h.build_id, a.build_revision AS revision,
                   a.snapshot #>> '{build,submission_status}' AS status
            FROM build_edit_history h JOIN build_history_states a ON a.id = h.after_state_id
            JOIN build_history_states b ON b.id = h.before_state_id
            WHERE b.snapshot #>> '{build,submission_status}' = '0'
              AND a.snapshot #>> '{build,submission_status}' IN ('1','2')
            ORDER BY h.build_id, h.id DESC
        ) outcome WHERE s.edit_vote_id IS NULL AND s.build_id = outcome.build_id
        AND s.decided_revision IS NULL AND s.decision = CASE outcome.status WHEN '1' THEN 'approved' ELSE 'rejected' END""")


def downgrade() -> None:
    op.drop_table_comment("build_submissions")
    op.drop_table_comment("build_image_mirrors")
    op.drop_column("build_submissions", "feedback")

"""Retain the submitted version across draft and vote cleanup."""

from alembic import op

revision = "a97c430d125e"
down_revision = "315f79ee0c2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE TABLE build_submissions (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        build_id bigint NOT NULL,
        submitter_account_id integer NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
        submitted_at timestamptz NOT NULL DEFAULT now(),
        finalization_job_id uuid UNIQUE,
        edit_vote_id bigint UNIQUE,
        base_revision bigint,
        content jsonb NOT NULL,
        CONSTRAINT build_submissions_origin_check CHECK (
            (finalization_job_id IS NOT NULL AND edit_vote_id IS NULL AND base_revision IS NULL) OR
            (finalization_job_id IS NULL AND edit_vote_id IS NOT NULL AND base_revision IS NOT NULL AND base_revision > 0)
        )
    )""")
    op.execute("CREATE INDEX build_submissions_submitter_idx ON build_submissions (submitter_account_id, submitted_at)")
    op.execute("""INSERT INTO build_submissions
        (build_id, submitter_account_id, submitted_at, finalization_job_id, content)
        SELECT r.build_id, d.owner_account_id, r.created_at, r.job_id, i.payload
        FROM submission_finalization_results r
        JOIN submission_finalization_inputs i USING (job_id)
        JOIN submission_finalization_jobs j ON j.id = r.job_id
        JOIN submission_drafts d ON d.id = j.draft_id""")
    op.execute("""INSERT INTO build_submissions
        (build_id, submitter_account_id, submitted_at, edit_vote_id, base_revision, content)
        SELECT b.build_id, v.author_account_id, v.created_at, v.id, b.proposal_revision,
               jsonb_build_object('patch', b.proposal_patch, 'changes', b.changes)
        FROM build_vote_sessions b JOIN vote_sessions v ON v.id = b.vote_session_id
        WHERE b.proposal_revision IS NOT NULL""")


def downgrade() -> None:
    op.drop_table("build_submissions")

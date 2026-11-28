"""Bind build votes to revisions and retain publication decisions."""

from alembic import op

revision = "6f41c27d3b9a"
down_revision = "a97c430d125e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE build_vote_sessions ADD COLUMN reviewed_revision bigint, ADD COLUMN submission_id uuid")
    op.execute("ALTER TABLE submission_finalization_results ADD COLUMN submission_id uuid")
    op.execute("""UPDATE submission_finalization_results r SET submission_id = s.id FROM build_submissions s
        WHERE s.finalization_job_id = r.job_id""")
    op.drop_constraint("build_submissions_origin_check", "build_submissions")
    op.create_check_constraint(
        "build_submissions_origin_check",
        "build_submissions",
        "(edit_vote_id IS NULL AND base_revision IS NULL) OR (finalization_job_id IS NULL AND edit_vote_id IS NOT NULL AND base_revision IS NOT NULL AND base_revision > 0)",
    )
    op.execute(
        "CREATE UNIQUE INDEX build_submissions_new_build_idx ON build_submissions (build_id) WHERE edit_vote_id IS NULL"
    )
    op.execute("""INSERT INTO build_submissions (build_id, submitter_account_id, submitted_at, content)
        SELECT b.id, b.submitter_account_id, coalesce(b.submission_time, now()), '{}'::jsonb
        FROM builds b WHERE b.submitter_account_id IS NOT NULL
        AND NOT EXISTS (SELECT 1 FROM build_submissions s WHERE s.build_id = b.id AND s.edit_vote_id IS NULL)""")
    op.execute("""UPDATE build_vote_sessions v SET submission_id = s.id FROM build_submissions s
        WHERE s.edit_vote_id = v.vote_session_id OR
        (v.proposal_revision IS NULL AND s.edit_vote_id IS NULL AND s.build_id = v.build_id)""")
    op.execute("""UPDATE build_vote_sessions v SET reviewed_revision = (
        SELECT a.build_revision FROM build_history_states a WHERE a.build_id = v.build_id AND a.captured_at <= session.created_at
        ORDER BY a.captured_at DESC, a.id DESC LIMIT 1) FROM vote_sessions session
        WHERE session.id = v.vote_session_id AND v.proposal_revision IS NULL""")
    op.execute(
        "ALTER TABLE build_submissions ADD COLUMN decision text NOT NULL DEFAULT 'pending', ADD COLUMN decided_revision bigint"
    )
    op.execute("CREATE INDEX build_submissions_build_idx ON build_submissions (build_id)")
    op.execute("""UPDATE build_submissions s SET decision = CASE b.submission_status WHEN 1 THEN 'approved' ELSE 'rejected' END
        FROM builds b WHERE s.build_id = b.id AND s.edit_vote_id IS NULL AND b.submission_status IN (1,2)""")
    op.execute("""UPDATE build_submissions s SET decision = CASE v.result WHEN 'denied' THEN 'rejected' ELSE 'cancelled' END
        FROM vote_sessions v WHERE v.id = s.edit_vote_id AND v.result IN ('denied','cancelled')""")
    op.execute("""UPDATE build_submissions s SET decision = 'approved', decided_revision = a.build_revision
        FROM build_edit_history h JOIN build_history_states a ON a.id = h.after_state_id
        WHERE h.source = 'build_edit_vote' AND h.correlation_kind = 'vote_session' AND h.correlation_id = s.edit_vote_id::text""")
    op.execute("""CREATE FUNCTION public.record_submission_decision() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_revision bigint;
    decided_status integer;
BEGIN
    IF TG_TABLE_NAME = 'vote_sessions' THEN
        IF NEW.result IN ('denied', 'cancelled') THEN
            UPDATE public.build_submissions SET decision = CASE NEW.result WHEN 'denied' THEN 'rejected' ELSE 'cancelled' END
            WHERE edit_vote_id = NEW.id AND decision = 'pending';
        END IF;
    ELSE
        SELECT build_revision, (snapshot #>> '{build,submission_status}')::integer
        INTO target_revision, decided_status FROM public.build_history_states WHERE id = NEW.after_state_id;
        IF NEW.operation = 'create' THEN
            INSERT INTO public.build_submissions (build_id, submitter_account_id, content)
            SELECT b.id, b.submitter_account_id, jsonb_strip_nulls(jsonb_build_object(
                'display_name', b.display_name, 'description', b.description, 'category', b.category,
                'version_compatibility', b.version_spec, 'capture_dimensions',
                jsonb_build_object('width', b.width, 'height', b.height, 'depth', b.depth)))
            FROM public.builds b WHERE b.id = NEW.build_id AND b.submitter_account_id IS NOT NULL
            ON CONFLICT (build_id) WHERE edit_vote_id IS NULL DO NOTHING;
        END IF;
        IF NEW.source = 'build_edit_vote' AND NEW.correlation_kind = 'vote_session' THEN
            UPDATE public.build_submissions SET decision = 'approved', decided_revision = target_revision
            WHERE edit_vote_id = NEW.correlation_id::bigint AND decision = 'pending';
        END IF;
        IF NEW.before_state_id IS NOT NULL THEN
            UPDATE public.build_submissions SET decision = 'stale'
            WHERE build_id = NEW.build_id AND edit_vote_id IS NOT NULL AND decision = 'pending'
                AND base_revision IS DISTINCT FROM target_revision;
            UPDATE public.vote_sessions v SET status = 'closed', result = 'cancelled'
            FROM public.build_vote_sessions t
            WHERE v.id = t.vote_session_id AND t.build_id = NEW.build_id AND v.status = 'open'
                AND coalesce(t.proposal_revision, t.reviewed_revision) IS DISTINCT FROM target_revision;
        END IF;
        IF NEW.operation = 'delete' THEN
            UPDATE public.build_submissions SET decision = 'cancelled'
            WHERE build_id = NEW.build_id AND edit_vote_id IS NULL AND decision = 'pending';
        ELSIF decided_status = 0 AND NEW.before_state_id IS NOT NULL THEN
            UPDATE public.build_submissions SET decision = 'pending', decided_revision = NULL
            WHERE build_id = NEW.build_id AND edit_vote_id IS NULL;
            PERFORM public.publish_domain_event('build.review_requested', 1::smallint, 'build', NEW.build_id, jsonb_build_object('revision', target_revision));
        ELSIF decided_status IN (1, 2) THEN
            UPDATE public.build_submissions SET decision = CASE decided_status WHEN 1 THEN 'approved' ELSE 'rejected' END,
                decided_revision = target_revision
            WHERE build_id = NEW.build_id AND edit_vote_id IS NULL AND decision = 'pending';
        END IF;
    END IF;
    RETURN NULL;
END;
$$;""")
    op.execute(
        """CREATE TRIGGER submission_decision_vote AFTER UPDATE OF result ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.record_submission_decision();"""
    )
    op.execute(
        """CREATE TRIGGER submission_decision_history AFTER INSERT ON public.build_edit_history FOR EACH ROW EXECUTE FUNCTION public.record_submission_decision();"""
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER submission_decision_vote ON vote_sessions")
    op.execute("DROP TRIGGER submission_decision_history ON build_edit_history")
    op.execute("DROP FUNCTION public.record_submission_decision()")
    op.execute("DELETE FROM build_submissions WHERE edit_vote_id IS NULL AND finalization_job_id IS NULL")
    op.drop_index("build_submissions_new_build_idx", table_name="build_submissions")
    op.drop_constraint("build_submissions_origin_check", "build_submissions")
    op.create_check_constraint(
        "build_submissions_origin_check",
        "build_submissions",
        "(finalization_job_id IS NOT NULL AND edit_vote_id IS NULL AND base_revision IS NULL) OR (finalization_job_id IS NULL AND edit_vote_id IS NOT NULL AND base_revision IS NOT NULL AND base_revision > 0)",
    )
    op.drop_index("build_submissions_build_idx", table_name="build_submissions")
    op.drop_column("build_submissions", "decision")
    op.drop_column("build_submissions", "decided_revision")
    op.drop_column("submission_finalization_results", "submission_id")
    op.drop_column("build_vote_sessions", "submission_id")
    op.drop_column("build_vote_sessions", "reviewed_revision")

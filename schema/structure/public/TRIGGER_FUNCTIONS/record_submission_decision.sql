CREATE FUNCTION public.record_submission_decision() RETURNS trigger
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
$$;


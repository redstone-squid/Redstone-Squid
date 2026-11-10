CREATE FUNCTION public.finish_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    pending public.build_edit_pending%ROWTYPE;
    final_snapshot jsonb;
    final_revision bigint;
    before_id bigint;
    after_id bigint;
    action text;
BEGIN
    SELECT * INTO pending FROM public.build_edit_pending
    WHERE transaction_id = NEW.transaction_id AND build_id = NEW.build_id;
    IF NOT FOUND THEN RETURN NULL; END IF;
    PERFORM set_config('squid.build_edit_finalized', txid_current()::text, true);
    final_snapshot := public.build_history_snapshot_v1(pending.build_id);
    SELECT revision INTO final_revision FROM public.builds WHERE id = pending.build_id;
    IF pending.before_snapshot IS DISTINCT FROM final_snapshot THEN
        IF pending.before_snapshot IS NOT NULL THEN
            SELECT s.id INTO before_id FROM public.build_edit_history h
            JOIN public.build_history_states s ON s.id = h.after_state_id
            WHERE h.id = (SELECT max(id) FROM public.build_edit_history WHERE build_id = pending.build_id)
                AND s.snapshot = pending.before_snapshot AND s.build_revision = pending.before_revision
                AND s.schema_version = 1;
            IF before_id IS NULL THEN
                INSERT INTO public.build_history_states(build_id, build_revision, schema_version, snapshot)
                VALUES (pending.build_id, pending.before_revision, 1, pending.before_snapshot) RETURNING id INTO before_id;
            END IF;
        END IF;
        IF final_snapshot IS NOT NULL THEN
            INSERT INTO public.build_history_states(build_id, build_revision, schema_version, snapshot)
            VALUES (pending.build_id, final_revision, 1, final_snapshot) RETURNING id INTO after_id;
        END IF;
        action := CASE WHEN before_id IS NULL THEN 'create' WHEN after_id IS NULL THEN 'delete'
                       ELSE pending.context->>'operation' END;
        INSERT INTO public.build_edit_history(build_id, operation_id, operation, source,
            actor_account_id, actor_system, correlation_kind, correlation_id, before_state_id, after_state_id)
        VALUES (pending.build_id, (pending.context->>'operation_id')::uuid, action, pending.context->>'source',
            (pending.context->>'actor_account_id')::integer, pending.context->>'actor_system',
            pending.context->>'correlation_kind', pending.context->>'correlation_id', before_id, after_id);
    END IF;
    DELETE FROM public.build_edit_pending WHERE transaction_id = pending.transaction_id AND build_id = pending.build_id;
    RETURN NULL;
END;
$$;


CREATE FUNCTION public.capture_build_edit(target_build_id bigint) RETURNS void
    LANGUAGE plpgsql
    AS $$
DECLARE
    context_value jsonb;
    previous_snapshot jsonb;
    previous_revision bigint;
BEGIN
    context_value := nullif(current_setting('squid.build_edit_context', true), '')::jsonb;
    IF context_value IS NULL OR (context_value->>'transaction_id')::bigint <> txid_current()
        OR current_setting('squid.build_edit_open', true) IS DISTINCT FROM 'true'
    THEN
        RAISE EXCEPTION 'Build changes require an active build editing scope' USING ERRCODE = '55000';
    END IF;
    IF current_setting('squid.build_edit_finalized', true) = txid_current()::text THEN
        RAISE EXCEPTION 'Build history was finalized; no further build changes are allowed' USING ERRCODE = '55000';
    END IF;
    IF EXISTS (SELECT 1 FROM public.build_edit_pending
               WHERE transaction_id = txid_current() AND build_id = target_build_id) THEN
        RETURN;
    END IF;
    -- Serialize every aggregate before taking its first pre-edit snapshot.
    SELECT revision INTO previous_revision FROM public.builds WHERE id = target_build_id FOR UPDATE;
    previous_snapshot := public.build_history_snapshot_v1(target_build_id);
    INSERT INTO public.build_edit_pending(transaction_id, build_id, context, before_snapshot, before_revision)
    VALUES (txid_current(), target_build_id, context_value, previous_snapshot, previous_revision);
END;
$$;

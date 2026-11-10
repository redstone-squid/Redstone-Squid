CREATE FUNCTION public.begin_build_edit(edit_context jsonb) RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF nullif(current_setting('squid.build_edit_context', true), '') IS NOT NULL THEN
        RAISE EXCEPTION 'Only one build editing scope is allowed per transaction' USING ERRCODE = '55000';
    END IF;
    IF jsonb_typeof(edit_context) IS DISTINCT FROM 'object'
        OR nullif(btrim(edit_context->>'source'), '') IS NULL
        OR num_nonnulls(edit_context->>'actor_account_id', edit_context->>'actor_system') <> 1
        OR (edit_context->>'actor_account_id')::integer <= 0
        OR (edit_context->>'actor_system' IS NOT NULL AND btrim(edit_context->>'actor_system') = '')
        OR (edit_context->>'correlation_kind' IS NULL) <> (edit_context->>'correlation_id' IS NULL)
        OR (edit_context->>'operation') IS NULL
        OR (edit_context->>'operation') NOT IN ('create', 'edit', 'moderation', 'recalculation', 'schematic', 'account_merge', 'delete')
        OR (edit_context->>'operation_id') IS NULL
    THEN
        RAISE EXCEPTION 'Invalid build edit context' USING ERRCODE = '22023';
    END IF;
    PERFORM (edit_context->>'operation_id')::uuid;
    PERFORM set_config('squid.build_edit_context',
        (edit_context || jsonb_build_object('transaction_id', txid_current()))::text, true);
    PERFORM set_config('squid.build_edit_open', 'true', true);
END;
$$;

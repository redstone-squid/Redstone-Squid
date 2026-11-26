-- Bulk deletes reach every result and holder row, so skip requests that would do nothing:
-- a deleted result with no document or pending request, and holders deleted with their result.
CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_key text;
BEGIN
    IF TG_TABLE_NAME = 'title_results' THEN
        target_key := 'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text;
        -- One statement, so a worker committing the document and its acknowledgement
        -- together cannot slip between the two checks.
        IF TG_OP = 'DELETE' AND NOT EXISTS (
            SELECT 1 FROM public.search_documents d
            WHERE d.resource_kind = 'record' AND d.source_key = target_key
            UNION ALL
            SELECT 1 FROM public.search_projection_queue q
            WHERE q.resource_kind = 'record' AND q.source_key = target_key
        ) THEN
            RETURN NULL;
        END IF;
        PERFORM public.upsert_search_projection_request(
            'record', target_key, CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END
        );
    ELSIF EXISTS (
        SELECT 1 FROM public.title_results r
        WHERE r.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END
    ) THEN
        PERFORM public.upsert_search_projection_request(
            'record',
            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,
            'upsert'
        );
    END IF;
    RETURN NULL;
END;
$$;

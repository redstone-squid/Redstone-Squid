CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_TABLE_NAME = 'title_results' THEN
        PERFORM public.upsert_search_projection_request(
            'record',
            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END)::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END
        );
    ELSE
        PERFORM public.upsert_search_projection_request(
            'record',
            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,
            'upsert'
        );
    END IF;
    RETURN NULL;
END;
$$;


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
    ELSIF TG_TABLE_NAME = 'title_holders' THEN
        PERFORM public.upsert_search_projection_request(
            'record',
            'result:' || (CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END)::text,
            'upsert'
        );
    ELSE
        PERFORM public.upsert_search_projection_request('record', 'result:' || rr.id::text, 'upsert')
        FROM public.title_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    RETURN NULL;
END;
$$;

-- Queue the records whose search documents a statement changed. Every change is an
-- upsert: the loader turns a record into a current or retired document, or deletes
-- the document of a record that is no longer searchable.
CREATE FUNCTION public.enqueue_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    record_ids uuid[];
BEGIN
    IF TG_TABLE_NAME IN ('title_results', 'title_holder_history') THEN
        IF TG_OP = 'INSERT' THEN
            record_ids := ARRAY(SELECT title_id FROM new_rows);
        ELSIF TG_OP = 'DELETE' THEN
            record_ids := ARRAY(SELECT title_id FROM old_rows);
        ELSE
            record_ids := ARRAY(SELECT title_id FROM new_rows UNION SELECT title_id FROM old_rows);
        END IF;
    ELSIF TG_TABLE_NAME IN ('title_holders', 'title_tied_builds') THEN
        -- Rows deleted with their result find no result here; the result's own trigger queues the record.
        IF TG_OP = 'INSERT' THEN
            record_ids := ARRAY(
                SELECT r.title_id FROM new_rows c JOIN public.title_results r ON r.id = c.result_id
            );
        ELSIF TG_OP = 'DELETE' THEN
            record_ids := ARRAY(
                SELECT r.title_id FROM old_rows c JOIN public.title_results r ON r.id = c.result_id
            );
        ELSE
            record_ids := ARRAY(
                SELECT r.title_id
                FROM (SELECT result_id FROM new_rows UNION SELECT result_id FROM old_rows) c
                JOIN public.title_results r ON r.id = c.result_id
            );
        END IF;
    ELSIF TG_OP = 'DELETE' THEN
        record_ids := ARRAY(SELECT public_id FROM old_rows);
    ELSE
        -- Records are updated by every recompute; only retirement and revival change their documents.
        record_ids := ARRAY(
            SELECT n.public_id
            FROM new_rows n JOIN old_rows o ON o.public_id = n.public_id
            WHERE n.retired_at IS DISTINCT FROM o.retired_at
        );
    END IF;
    PERFORM public.upsert_search_projection_requests(
        'record', ARRAY(SELECT 'record:' || id::text FROM unnest(record_ids) AS ids(id)), 'upsert'
    );
    RETURN NULL;
END;
$$;

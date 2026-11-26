-- Records show their holders' build titles and credits, and match the names of their
-- categories' tags. When those documents change, queue the records that read them.
-- The store skips unchanged documents, so an edit that changes nothing a record reads
-- still queues its records, but their documents are then left alone.
CREATE FUNCTION public.enqueue_dependent_record_search() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    kinds text[];
    keys text[];
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT array_agg(resource_kind), array_agg(source_key) INTO kinds, keys FROM new_rows;
    ELSIF TG_OP = 'DELETE' THEN
        SELECT array_agg(resource_kind), array_agg(source_key) INTO kinds, keys FROM old_rows;
    ELSE
        SELECT array_agg(n.resource_kind), array_agg(n.source_key) INTO kinds, keys
        FROM new_rows n JOIN old_rows o ON o.id = n.id
        WHERE (n.title, n.status, n.document_data) IS DISTINCT FROM (o.title, o.status, o.document_data);
    END IF;
    IF kinds IS NULL THEN
        RETURN NULL;
    END IF;
    -- CASE keeps the casts away from keys of other kinds.
    PERFORM public.upsert_search_projection_requests(
        'record',
        ARRAY(
            SELECT 'record:' || r.title_id::text
            FROM unnest(kinds, keys) AS changed(kind, key)
            JOIN public.title_holders h
                ON h.build_id = CASE WHEN changed.kind = 'build' AND changed.key ~ '^[0-9]+$' THEN changed.key::bigint END
            JOIN public.title_results r ON r.id = h.result_id
            UNION
            SELECT d.source_key
            FROM unnest(kinds, keys) AS changed(kind, key)
            JOIN public.search_values v
                ON v.field_name = 'category_tag_id'
                AND v.numeric_value = CASE
                    WHEN changed.kind = 'metadata' AND changed.key ~ '^tag:[0-9]+$'
                    THEN split_part(changed.key, ':', 2)::numeric
                END
            JOIN public.search_documents d ON d.id = v.document_id AND d.resource_kind = 'record'
        ),
        'upsert'
    );
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'tag_definitions' THEN 'tag'
        WHEN 'tag_aliases' THEN 'tag'
        WHEN 'creator_aliases' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'tag_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.tag_id ELSE NEW.tag_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'tag_aliases' THEN
        target_action := 'delete';
    END IF;

    PERFORM public.upsert_search_projection_request(
        'metadata', target_kind || ':' || target_id::text, target_action
    );

    -- Builds show the names of their tags, creators, and versions, so their documents are stale too.
    IF TG_TABLE_NAME IN ('tag_definitions', 'tag_aliases') THEN
        PERFORM
            public.upsert_search_projection_request('build', assignment.build_id::text, 'upsert'),
            public.upsert_discord_sync_request('build', assignment.build_id::text, 'refresh')
        FROM public.build_tag_assignments assignment
        WHERE assignment.tag_id = target_id;
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        PERFORM public.upsert_search_projection_request('build', bc.build_id::text, 'upsert')
        FROM public.build_creators bc
        WHERE bc.alias_id = target_id;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        PERFORM public.upsert_search_projection_request('build', bv.build_id::text, 'upsert')
        FROM public.build_versions bv
        WHERE bv.version_id = target_id;
    END IF;
    RETURN NULL;
END;
$$;

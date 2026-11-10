CREATE FUNCTION public.capture_build_edit_trigger() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    old_value jsonb;
    new_value jsonb;
    ignored text[] := ARRAY[]::text[];
    build_ids bigint[];
    target bigint;
BEGIN
    IF TG_OP = 'TRUNCATE' THEN
        RAISE EXCEPTION 'Use DELETE inside a build editing scope instead of TRUNCATE' USING ERRCODE = '55000';
    END IF;
    old_value := CASE WHEN TG_OP = 'INSERT' THEN NULL ELSE to_jsonb(OLD) END;
    new_value := CASE WHEN TG_OP = 'DELETE' THEN NULL ELSE to_jsonb(NEW) END;
    IF TG_TABLE_NAME = 'builds' THEN
        ignored := ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at', 'revision', 'edited_time'];
    ELSIF TG_TABLE_NAME = 'build_tag_assignments' THEN
        ignored := ARRAY['created_at', 'updated_at'];
    ELSIF TG_TABLE_NAME = 'build_schematics' THEN
        -- The history contract retains attachment/publication metadata, not recomputable analyses.
        SELECT jsonb_object_agg(key, value) INTO old_value FROM jsonb_each(old_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
        SELECT jsonb_object_agg(key, value) INTO new_value FROM jsonb_each(new_value)
        WHERE key = ANY(ARRAY['id','build_id','file_sha256','is_primary','original_filename',
            'uploaded_by_account_id','visibility','license_code','rights_attested_at',
            'rights_attested_by_account_id','published_at','withdrawn_at']);
    ELSIF TG_TABLE_NAME = 'creator_aliases' THEN
        ignored := ARRAY['normalized_name','claimed_at','claim_method','created_at'];
    END IF;
    IF (old_value - ignored) IS NOT DISTINCT FROM (new_value - ignored) THEN
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    IF TG_TABLE_NAME = 'creator_aliases' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_creators
        WHERE alias_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'versions' THEN
        SELECT array_agg(build_id ORDER BY build_id) INTO build_ids FROM public.build_versions
        WHERE version_id IN ((old_value->>'id')::bigint, (new_value->>'id')::bigint);
    ELSIF TG_TABLE_NAME = 'builds' THEN
        build_ids := ARRAY[(old_value->>'id')::bigint, (new_value->>'id')::bigint];
    ELSE
        build_ids := ARRAY[(old_value->>'build_id')::bigint, (new_value->>'build_id')::bigint];
    END IF;
    FOR target IN SELECT DISTINCT value FROM unnest(build_ids) AS value WHERE value IS NOT NULL ORDER BY value LOOP
        PERFORM public.capture_build_edit(target);
    END LOOP;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$;

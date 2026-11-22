-- Queue the builds credited to an identity whose account or visibility changed.
-- Build documents index the public IDs of the accounts their credits publicly belong to.
CREATE FUNCTION public.enqueue_identity_credited_builds() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM public.upsert_search_projection_request('build', credited.build_id::text, 'upsert')
    FROM (
        SELECT DISTINCT bc.build_id
        FROM public.build_credits bc
        JOIN (VALUES (OLD.provider, OLD.subject), (NEW.provider, NEW.subject)) AS changed(provider, subject)
            ON bc.subject = changed.subject
            AND bc.kind = CASE changed.provider
                WHEN 'discord' THEN 'discord'
                WHEN 'java' THEN 'minecraft_java'
                WHEN 'bedrock' THEN 'minecraft_bedrock'
            END
        ORDER BY bc.build_id
    ) AS credited;
    RETURN NULL;
END;
$$;

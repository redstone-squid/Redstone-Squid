CREATE FUNCTION public.build_credit_accounts(target_build_id bigint DEFAULT NULL)
RETURNS TABLE(build_id bigint, "position" smallint, account_id integer, name text, role text)
LANGUAGE sql STABLE AS $$
    SELECT c.build_id, c.position, coalesce(i.account_id, a.account_id), c.name, c.role
    FROM public.build_credits c
    LEFT JOIN public.creator_aliases a ON a.id = c.alias_id
    LEFT JOIN public.account_identities i ON i.subject = c.subject AND i.provider = CASE c.kind
        WHEN 'discord' THEN 'discord'
        WHEN 'minecraft_java' THEN 'java'
        WHEN 'minecraft_bedrock' THEN 'bedrock'
    END
    WHERE (target_build_id IS NULL OR c.build_id = target_build_id)
      AND coalesce(i.account_id, a.account_id) IS NOT NULL;
$$;

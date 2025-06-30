CREATE
OR REPLACE FUNCTION public.find_restriction_ids (search_terms text[]) RETURNS TABLE (
  id smallint,
  build_category text,
  name text,
  type text
) LANGUAGE plpgsql AS $function$
BEGIN
    RETURN QUERY
    SELECT DISTINCT *
    FROM (
        SELECT r.id, r.build_category, r.name, r.type  -- prevent collision with the TABLE above
        FROM restrictions r
        WHERE r.name = ANY(search_terms)

        UNION

        SELECT restriction_id, r.build_category, alias, r.type
        FROM restriction_aliases JOIN restrictions r ON restriction_id = r.id
        WHERE alias = ANY(search_terms)
    ) s;
END;
$function$;
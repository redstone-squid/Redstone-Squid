CREATE
OR REPLACE FUNCTION public.get_quantified_version_names () RETURNS TABLE (id smallint, quantified_name text) LANGUAGE plpgsql AS $function$
BEGIN
    RETURN QUERY
    SELECT
        v.id,
        v.edition || ' ' ||
        v.major_version || '.' ||
        v.minor_version || '.' ||
        v.patch_number AS quantified_name
    FROM
        versions v;
END;
$function$;
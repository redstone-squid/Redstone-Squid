-- existing build_versions data are manually edited

CREATE OR REPLACE FUNCTION get_quantified_version_names()
RETURNS TABLE(id SMALLINT, quantified_name TEXT) AS $$
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
$$ LANGUAGE plpgsql;

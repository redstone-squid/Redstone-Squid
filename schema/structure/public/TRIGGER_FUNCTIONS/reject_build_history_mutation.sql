CREATE FUNCTION public.reject_build_history_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'build history is append-only' USING ERRCODE = '55000';
END;
$$;


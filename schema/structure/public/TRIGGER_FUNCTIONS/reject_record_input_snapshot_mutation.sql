CREATE FUNCTION public.reject_record_input_snapshot_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'Record input snapshots are immutable' USING ERRCODE = '23514';
END;
$$;


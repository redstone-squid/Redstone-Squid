CREATE FUNCTION public.reject_door_range_snapshot_mutation() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION 'Door range snapshots are immutable' USING ERRCODE = '23514';
END;
$$;


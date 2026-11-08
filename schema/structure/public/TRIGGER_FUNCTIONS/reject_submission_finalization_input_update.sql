CREATE FUNCTION public.reject_submission_finalization_input_update() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    RAISE EXCEPTION USING
        ERRCODE = 'check_violation',
        MESSAGE = 'submission finalization inputs are immutable',
        SCHEMA = 'public',
        TABLE = 'submission_finalization_inputs';
END;
$$;


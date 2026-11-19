CREATE FUNCTION public.close_build_edit() RETURNS void
    LANGUAGE plpgsql
    AS $$
BEGIN
    PERFORM set_config('squid.build_edit_open', 'false', true);
END;
$$;

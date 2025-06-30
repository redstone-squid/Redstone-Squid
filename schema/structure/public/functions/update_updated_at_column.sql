CREATE
OR REPLACE FUNCTION public.update_updated_at_column () RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$function$;
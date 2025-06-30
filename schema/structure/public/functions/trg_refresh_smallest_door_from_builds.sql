CREATE
OR REPLACE FUNCTION public.trg_refresh_smallest_door_from_builds () RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.id);
    ELSE                               -- INSERT or UPDATE
        CALL public.refresh_smallest_after_door_delete(OLD.id);
        CALL public.refresh_smallest_for_door_insert(NEW.id);
    END IF;
    RETURN NULL;
END;
$function$;
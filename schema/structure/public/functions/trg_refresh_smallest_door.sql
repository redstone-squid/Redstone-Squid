CREATE
OR REPLACE FUNCTION public.trg_refresh_smallest_door () RETURNS trigger LANGUAGE plpgsql AS $function$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);

    ELSIF TG_OP = 'INSERT' THEN
        -- remove the stale rows for *this* build first
        -- The reason why we need to delete the old winners even for INSERT is that
        -- here, INSERT can also mean "insert a new type/restriction" for an existing door,
        CALL public.refresh_smallest_after_door_delete(NEW.build_id);
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);

    ELSE -- UPDATE
        -- First remove the “old” winners, then add the “new” ones
        CALL public.refresh_smallest_after_door_delete(OLD.build_id);
        CALL public.refresh_smallest_for_door_insert(NEW.build_id);

    END IF;
    RETURN NULL;
END;
$function$;
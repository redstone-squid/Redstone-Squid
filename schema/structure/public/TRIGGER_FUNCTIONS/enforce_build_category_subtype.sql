CREATE FUNCTION public.enforce_build_category_subtype() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_ids bigint[];
    target_id bigint;
    build_category text;
    expected_table text;
    door_count integer;
    extender_count integer;
    utility_count integer;
    entrance_count integer;
    other_count integer;
BEGIN
    -- Each build has exactly one row in the subtype table named by its category, which is what
    -- SQLAlchemy's joined-table inheritance needs to load it. Deferred, because the ORM inserts
    -- the builds row before the subtype row.
    IF TG_TABLE_NAME = 'builds' THEN
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END];
        IF TG_OP = 'UPDATE' AND OLD.id IS DISTINCT FROM NEW.id THEN
            target_ids := array_append(target_ids, OLD.id);
        END IF;
    ELSE
        target_ids := ARRAY[CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END];
        IF TG_OP = 'UPDATE' AND OLD.build_id IS DISTINCT FROM NEW.build_id THEN
            target_ids := array_append(target_ids, OLD.build_id);
        END IF;
    END IF;

    FOREACH target_id IN ARRAY target_ids LOOP
        -- See enforce_vote_session_kind_subtype for why NO KEY UPDATE.
        SELECT category INTO build_category FROM public.builds WHERE id = target_id FOR NO KEY UPDATE;
        IF NOT FOUND THEN
            CONTINUE;
        END IF;

        SELECT
            (SELECT count(*) FROM public.doors WHERE build_id = target_id),
            (SELECT count(*) FROM public.extenders WHERE build_id = target_id),
            (SELECT count(*) FROM public.utilities WHERE build_id = target_id),
            (SELECT count(*) FROM public.entrances WHERE build_id = target_id),
            (SELECT count(*) FROM public.other_builds WHERE build_id = target_id)
        INTO door_count, extender_count, utility_count, entrance_count, other_count;

        expected_table := CASE build_category
            WHEN 'Door' THEN 'doors'
            WHEN 'Extender' THEN 'extenders'
            WHEN 'Utility' THEN 'utilities'
            WHEN 'Entrance' THEN 'entrances'
            WHEN 'Other' THEN 'other_builds'
        END;

        IF NOT (
            door_count + extender_count + utility_count + entrance_count + other_count = 1
            AND CASE expected_table
                WHEN 'doors' THEN door_count
                WHEN 'extenders' THEN extender_count
                WHEN 'utilities' THEN utility_count
                WHEN 'entrances' THEN entrance_count
                WHEN 'other_builds' THEN other_count
            END = 1
        ) THEN
            RAISE EXCEPTION USING
                ERRCODE = 'check_violation',
                MESSAGE = format(
                    'build %s category %s has subtype counts doors=%s, extenders=%s, utilities=%s, '
                    'entrances=%s, other_builds=%s',
                    target_id, build_category, door_count, extender_count, utility_count, entrance_count,
                    other_count
                ),
                SCHEMA = 'public',
                TABLE = 'builds',
                CONSTRAINT = 'builds_category_subtype_check';
        END IF;
    END LOOP;

    RETURN NULL;
END;
$$;


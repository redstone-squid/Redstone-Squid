CREATE
OR REPLACE FUNCTION public.sync_new_restriction () RETURNS trigger LANGUAGE plpgsql AS $function$
DECLARE
    -- string we are searching for
    b_restriction        text;

    -- id that will go into build_restrictions
    b_restriction_id     int;

    -- category / type of the *restriction*
    r_category           text;
    r_type               text;

    -- json key inside unknown_restrictions matching r_type
    json_key             text;
BEGIN
    -- 1. figure out category & type (alias rows don’t have them)
    IF TG_TABLE_NAME = 'restrictions' THEN
        b_restriction    := NEW.name;
        b_restriction_id := NEW.id;
        r_category       := NEW.build_category;
        r_type           := NEW.type;
    ELSIF TG_TABLE_NAME = 'restriction_aliases' THEN
        b_restriction    := NEW.alias;
        b_restriction_id := NEW.restriction_id;

        SELECT r.build_category, r.type
        INTO   r_category, r_type
        FROM   restrictions r
        WHERE  r.id = NEW.restriction_id;
    ELSE
        RAISE EXCEPTION 'sync_new_restriction() fired by unexpected table %', TG_TABLE_NAME;
    END IF;

    -- 2. map type → correct json key
    json_key := CASE r_type
                  WHEN 'component'        THEN 'component_restrictions'
                  WHEN 'wiring-placement' THEN 'wiring_placement_restrictions'
                  WHEN 'miscellaneous'    THEN 'miscellaneous_restrictions'
                END;

    IF json_key IS NULL THEN
        RAISE NOTICE 'Restriction type % is unsupported – skipped', r_type;
        RETURN NULL;
    END IF;

    -- 3. touch only builds with same category & containing the string
    WITH affected AS (
        SELECT b.id,
               (
                   WITH elems AS (
                       SELECT jsonb_array_elements_text(
                                  b.extra_info -> 'unknown_restrictions' -> json_key
                              ) AS val
                   ),
                   kept AS (
                       SELECT jsonb_agg(to_jsonb(val)) AS arr
                       FROM   elems
                       WHERE  lower(val) <> lower(b_restriction)
                   )
                   SELECT CASE
                              WHEN (SELECT arr FROM kept) IS NULL THEN
                                  CASE
                                      WHEN ((b.extra_info -> 'unknown_restrictions') - json_key) = '{}'::jsonb THEN
                                          b.extra_info - 'unknown_restrictions'
                                      ELSE
                                          jsonb_set(
                                              b.extra_info,
                                              '{unknown_restrictions}',
                                              (b.extra_info -> 'unknown_restrictions') - json_key,
                                              TRUE
                                          )
                                  END
                              ELSE
                                  jsonb_set(
                                      b.extra_info,
                                      ARRAY['unknown_restrictions', json_key],
                                      (SELECT arr FROM kept),
                                      TRUE
                                  )
                          END
               ) AS new_extra
        FROM   builds b
        WHERE  b.category = r_category
          AND  EXISTS (
              SELECT 1
              FROM   jsonb_array_elements_text(
                         b.extra_info -> 'unknown_restrictions' -> json_key
                     ) AS t(val)
              WHERE  lower(val) = lower(b_restriction)
          )
    ),
    changed AS (
        UPDATE builds b
        SET    extra_info = a.new_extra
        FROM   affected a
        WHERE  b.id = a.id
        RETURNING b.id
    )
    -- 4. link builds to the new restriction (ignore dupes)
    INSERT INTO build_restrictions (build_id, restriction_id)
    SELECT id, b_restriction_id
    FROM   changed
    ON CONFLICT DO NOTHING;

    RETURN NULL;  -- AFTER trigger
END;
$function$;
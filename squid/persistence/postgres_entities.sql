-- Functions and triggers owned by Alembic. Edit this file, then autogenerate a revision.

CREATE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    DELETE FROM vote_sessions
    WHERE id IN (
        SELECT vote_sessions.id
        FROM vote_sessions vs
        LEFT JOIN build_vote_sessions bvs ON vs.id = bvs.vote_session_id
        LEFT JOIN delete_log_vote_sessions dvs ON vs.id = dvs.vote_session_id
        WHERE bvs.vote_session_id IS NULL AND dvs.vote_session_id IS NULL
    );
    RETURN NULL; -- Statement-level triggers do not use OLD or NEW
END;
$$;

CREATE FUNCTION public.find_restriction_ids(search_terms text[]) RETURNS TABLE(id smallint, build_category text, name text, type text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT DISTINCT *
    FROM (
        SELECT r.id, r.build_category, r.name, r.type  -- prevent collision with the TABLE above
        FROM restrictions r
        WHERE r.name = ANY(search_terms)

        UNION

        SELECT restriction_id, r.build_category, alias, r.type
        FROM restriction_aliases JOIN restrictions r ON restriction_id = r.id
        WHERE alias = ANY(search_terms)
    ) s;
END;
$$;

CREATE FUNCTION public.get_outdated_messages(server_id_input bigint) RETURNS SETOF public.messages
    LANGUAGE plpgsql
    AS $$begin
    return query select messages.*
    from messages join builds
    on (messages.submission_id = builds.submission_id)
    where messages.last_updated < builds.last_update
    and messages.server_id = server_id_input
    and builds.submission_status = 1;  -- accepted
  end;$$;

CREATE FUNCTION public.get_quantified_version_names() RETURNS TABLE(id smallint, quantified_name text)
    LANGUAGE plpgsql
    AS $$
BEGIN
    RETURN QUERY
    SELECT
        v.id,
        v.edition || ' ' ||
        v.major_version || '.' ||
        v.minor_version || '.' ||
        v.patch_number AS quantified_name
    FROM
        versions v;
END;
$$;

CREATE FUNCTION public.get_unsent_builds(server_id_input bigint) RETURNS SETOF public.builds
    LANGUAGE plpgsql
    AS $$
  begin
    return query select *
    from builds
    where id not in (
      select build_id
      from messages
      where server_id = server_id_input
      )
    and submission_status = 1;  -- accepted
  end;
$$;

CREATE FUNCTION public.power_set_max(txt text[], max_k integer DEFAULT 8) RETURNS SETOF text[]
    LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
    AS $$
DECLARE
    n     int := array_length(txt, 1);
    mask  int;
BEGIN
    IF n IS NULL OR n = 0 THEN
        RETURN NEXT ARRAY[]::text[];
        RETURN;
    END IF;

    FOR mask IN 0 .. (1 << n) - 1 LOOP
        -- skip masks with more than max_k bits set
        IF (
             SELECT COUNT(*)
             FROM   generate_series(0, n - 1) g
             WHERE  ((mask >> g) & 1) = 1
           ) > max_k THEN
            CONTINUE;
        END IF;

        RETURN NEXT coalesce(                      -- fall back to empty array
            (SELECT array_agg(txt[i] ORDER BY i)
             FROM generate_subscripts(txt, 1) AS i
             WHERE (mask >> (i - 1)) & 1 = 1),
            ARRAY[]::text[]
        );
    END LOOP;
END;
$$;

CREATE FUNCTION public.set_locked_at() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
begin
  if new.is_locked then
    new.locked_at := now();
  else
    new.locked_at := null;
  end if;
  return new;
end;
$$;

CREATE FUNCTION public.sync_new_restriction() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION public.trg_refresh_smallest_door() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;

CREATE FUNCTION public.trg_refresh_smallest_door_from_builds() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        CALL public.refresh_smallest_after_door_delete(OLD.id);
    ELSE                               -- INSERT or UPDATE
        CALL public.refresh_smallest_after_door_delete(OLD.id);
        CALL public.refresh_smallest_for_door_insert(NEW.id);
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.update_updated_at_column() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE FUNCTION public.enqueue_build_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_build_id bigint;
    target_action text := 'upsert';
BEGIN
    IF TG_TABLE_NAME = 'builds' THEN
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        IF TG_OP = 'DELETE' THEN
            target_action := 'delete';
        END IF;
    ELSE
        target_build_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.build_id ELSE NEW.build_id END;
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('build', target_build_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_metadata_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_id bigint;
    target_kind text;
    target_action text := 'upsert';
BEGIN
    target_kind := CASE TG_TABLE_NAME
        WHEN 'restrictions' THEN 'restriction'
        WHEN 'restriction_aliases' THEN 'restriction'
        WHEN 'types' THEN 'type'
        WHEN 'users' THEN 'creator'
        WHEN 'versions' THEN 'version'
    END;
    IF TG_TABLE_NAME = 'restriction_aliases' THEN
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.restriction_id ELSE NEW.restriction_id END;
    ELSE
        target_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    END IF;
    IF TG_OP = 'DELETE' AND TG_TABLE_NAME <> 'restriction_aliases' THEN
        target_action := 'delete';
    END IF;

    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('metadata', target_kind || ':' || target_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;

    IF TG_TABLE_NAME IN ('restrictions', 'restriction_aliases') THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', br.build_id::text, 'upsert', now()
        FROM public.build_restrictions br
        WHERE br.restriction_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'types' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bt.build_id::text, 'upsert', now()
        FROM public.build_types bt
        WHERE bt.type_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'users' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bc.build_id::text, 'upsert', now()
        FROM public.build_creators bc
        WHERE bc.user_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'versions' THEN
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'build', bv.build_id::text, 'upsert', now()
        FROM public.build_versions bv
        WHERE bv.version_id = target_id
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_legacy_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_record_id bigint;
    target_action text := 'upsert';
BEGIN
    target_record_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.record_id ELSE NEW.record_id END;
    IF TG_OP = 'DELETE' THEN
        target_action := 'delete';
    END IF;
    INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
    VALUES ('record', 'legacy-smallest:' || target_record_id::text, target_action, now())
    ON CONFLICT (resource_kind, source_key) DO UPDATE
    SET action = EXCLUDED.action,
        enqueued_at = EXCLUDED.enqueued_at,
        attempts = 0,
        locked_at = NULL,
        last_error = NULL;
    RETURN NULL;
END;
$$;

CREATE FUNCTION public.enqueue_computed_record_search_projection() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    target_result_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'record_results' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES (
            'record',
            'result:' || target_result_id::text,
            CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'upsert' END,
            now()
        )
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = EXCLUDED.action, enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSIF TG_TABLE_NAME = 'record_result_holders' THEN
        target_result_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.result_id ELSE NEW.result_id END;
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        VALUES ('record', 'result:' || target_result_id::text, 'upsert', now())
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    ELSE
        INSERT INTO public.search_projection_queue (resource_kind, source_key, action, enqueued_at)
        SELECT 'record', 'result:' || rr.id::text, 'upsert', now()
        FROM public.record_results rr
        WHERE rr.run_id = CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END
        ON CONFLICT (resource_kind, source_key) DO UPDATE
        SET action = 'upsert', enqueued_at = EXCLUDED.enqueued_at, locked_at = NULL;
    END IF;
    RETURN NULL;
END;
$$;

CREATE TRIGGER build_restrictions_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();

CREATE TRIGGER build_types_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();

CREATE TRIGGER builds_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door_from_builds();

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds AFTER DELETE ON public.builds FOR EACH STATEMENT EXECUTE FUNCTION public.delete_orphaned_build_vote_sessions_after_builds_delete();

CREATE TRIGGER doors_refresh_smallest_door AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.trg_refresh_smallest_door();

CREATE TRIGGER set_locked_at BEFORE UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.set_locked_at();

CREATE TRIGGER trg_sync_on_tag AFTER INSERT ON public.restrictions FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();

CREATE TRIGGER trg_sync_on_tag_alias AFTER INSERT ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.sync_new_restriction();

CREATE TRIGGER update_messages_updated_at BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

CREATE TRIGGER builds_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.builds FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER doors_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.doors FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER extenders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.extenders FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_types_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_types FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER build_creators_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.build_creators FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_search_projection();

CREATE TRIGGER restrictions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.restrictions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER restriction_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.restriction_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER types_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.types FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER users_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.users FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER versions_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.versions FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

CREATE TRIGGER smallest_door_records_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.smallest_door_records FOR EACH ROW EXECUTE FUNCTION public.enqueue_legacy_record_search_projection();

CREATE TRIGGER record_results_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.record_results FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

CREATE TRIGGER record_result_holders_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.record_result_holders FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

CREATE TRIGGER record_computation_runs_enqueue_search AFTER INSERT OR DELETE OR UPDATE OF is_active ON public.record_computation_runs FOR EACH ROW EXECUTE FUNCTION public.enqueue_computed_record_search_projection();

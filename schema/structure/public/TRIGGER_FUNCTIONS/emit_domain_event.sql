CREATE FUNCTION public.emit_domain_event() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
        DECLARE
            target_type text;
            target_kind text;
            target_id bigint;
            target_payload jsonb;
            target_schema_version integer := 1;
        BEGIN
            -- Unlike discord_sync_queue, this log records transitions, so an UPDATE that
            -- rewrites a column to the value it already held must not produce an event.
            IF TG_TABLE_NAME = 'builds' THEN
                IF TG_OP = 'INSERT' THEN
                    target_kind := 'build';
                    target_id := NEW.id;
                    target_type := 'build.submitted';
                    target_schema_version := 2;
                    target_payload := jsonb_build_object(
                        'status', NEW.submission_status,
                        'submitter_account_id', NEW.submitter_account_id,
                        'category', NEW.category
                    );
                    PERFORM public.publish_domain_event(
                        target_type, target_schema_version, target_kind, target_id, target_payload
                    );
                    RETURN NULL;
                END IF;
                IF OLD.submission_status IS NOT DISTINCT FROM NEW.submission_status THEN RETURN NULL; END IF;
                target_kind := 'build';
                target_id := NEW.id;
                target_schema_version := 3;
                IF NEW.submission_status = 1 THEN
                    target_type := 'build.confirmed';
                ELSIF NEW.submission_status = 2 THEN
                    target_type := 'build.denied';
                ELSE
                    RETURN NULL;
                END IF;
                target_payload := jsonb_build_object(
                    'previous_status', OLD.submission_status,
                    'status', NEW.submission_status,
                    'submitter_account_id', NEW.submitter_account_id,
                    'category', NEW.category,
                    'first_confirmation', NEW.submission_status = 1 AND NOT EXISTS (
                        SELECT 1
                        FROM public.domain_events
                        WHERE aggregate_kind = 'build'
                          AND aggregate_id = NEW.id
                          AND event_type = 'build.confirmed'
                    )
                );
            ELSE
                IF OLD.status IS NOT DISTINCT FROM NEW.status OR NEW.status <> 'closed' THEN RETURN NULL; END IF;
                target_kind := 'vote_session';
                target_id := NEW.id;
                target_type := 'vote_session.closed';
                target_payload := jsonb_build_object('kind', NEW.kind, 'result', NEW.result);
            END IF;

            PERFORM public.publish_domain_event(
                target_type, target_schema_version, target_kind, target_id, target_payload
            );

            RETURN NULL;
        END;
        $$;


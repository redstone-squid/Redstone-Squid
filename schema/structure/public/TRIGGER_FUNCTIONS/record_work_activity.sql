CREATE FUNCTION public.record_work_activity() RETURNS trigger
    LANGUAGE plpgsql
    AS $_$
DECLARE
    attribution jsonb := convert_from(decode(nullif(current_setting('squid.work_attribution', true), ''), 'base64'), 'UTF8')::jsonb;
    item jsonb;
    keys text;
    acquired_claim text;
    transition text := lower(TG_OP);
BEGIN
    -- Only project configured key columns. Job rows can contain large private payloads.
    SELECT string_agg(format('($1).%I AS %I', k, k), ', ')
    INTO keys FROM unnest(TG_ARGV[1:TG_NARGS - 1]) k;

    IF TG_OP = 'DELETE' THEN
        EXECUTE format('SELECT to_jsonb(r) FROM (SELECT %s) r', keys) INTO item USING OLD;
    ELSE
        EXECUTE format('SELECT to_jsonb(r) FROM (SELECT %s) r', keys) INTO item USING NEW;
        IF TG_ARGV[0] = 'preparation' THEN
            IF OLD.preparation_retry_at IS NULL THEN
                transition := 'insert';
            ELSIF NEW.preparation_retry_at IS NULL THEN
                transition := 'delete';
            ELSIF NEW.preparation_attempt IS DISTINCT FROM OLD.preparation_attempt AND NEW.preparation_attempt IS NOT NULL THEN
                acquired_claim := encode(sha256(uuid_send(NEW.preparation_attempt)), 'hex');
                transition := 'claim';
            END IF;
        ELSIF TG_ARGV[0] = 'queue' THEN
            IF NEW.claim_token IS NOT NULL THEN
                IF TG_OP = 'UPDATE' AND NEW.claim_token IS NOT DISTINCT FROM OLD.claim_token THEN
                    -- Lease renewal has the same executor and cause as its acquisition.
                    RETURN NULL;
                END IF;
                acquired_claim := encode(sha256(uuid_send(NEW.claim_token)), 'hex');
                IF TG_OP = 'UPDATE' THEN
                    transition := 'claim';
                END IF;
            END IF;
        END IF;
    END IF;

    INSERT INTO public.work_activity
        (relation, item, transition, acquired_claim, parent_claim, invocation_id,
         operator, effective, credential_id, impersonation_session_id, impersonation_use_id)
    VALUES (TG_TABLE_NAME, item, transition, acquired_claim, attribution->>'parent_claim',
            (attribution->>'invocation_id')::uuid, attribution->>'operator', attribution->>'effective',
            attribution->>'credential_id', (attribution->>'impersonation_session_id')::uuid,
            (attribution->>'impersonation_use_id')::uuid);
    RETURN NULL;
END;
$_$;


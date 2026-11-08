CREATE FUNCTION public.bump_permission_epoch() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_version bigint;
BEGIN
    -- Statement-level, so a bulk grant bumps once rather than per row.
    UPDATE public.permission_epoch
    SET version = version + 1, updated_at = now()
    WHERE id = 1
    RETURNING version INTO new_version;

    -- Same contract as squid_domain_events: NOTIFY arrives only after commit and
    -- is a latency hint. Watchers poll permission_epoch as the durable signal, so
    -- a dropped notification costs latency rather than correctness.
    --
    -- The singleton row is created by the migration that installs this trigger,
    -- so a missing one means someone deleted it. Skip the notify rather than
    -- error: a permission write failing outright would be far worse than a stale
    -- cache, and the watchers' wall-clock backstop still bounds the staleness.
    IF new_version IS NOT NULL THEN
        PERFORM pg_notify('squid_permissions', new_version::text);
    END IF;
    RETURN NULL;
END;
$$;


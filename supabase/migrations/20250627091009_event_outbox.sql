-- 1. Durable event storage
CREATE TABLE event_outbox (
    id            BIGSERIAL PRIMARY KEY,
    aggregate     TEXT        NOT NULL,      -- e.g. 'order'
    aggregate_id  BIGINT      NOT NULL,      -- e.g. 42
    type          TEXT        NOT NULL,      -- e.g. 'order.created'
    payload       JSONB       NOT NULL,      -- full details
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at  TIMESTAMPTZ                  -- filled after the bot handles it
);

-- 2. AFTER-INSERT trigger that nudges the bot
CREATE FUNCTION notify_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- Only send the *row id*; NOTIFY payloads max out at 8 kB
    PERFORM pg_notify('domain_events', NEW.id::text);
    RETURN NEW;
END;
$$;

CREATE TRIGGER event_outbox_notify
AFTER INSERT ON event_outbox
FOR EACH ROW EXECUTE FUNCTION notify_event();

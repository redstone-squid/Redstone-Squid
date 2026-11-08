CREATE FUNCTION public.publish_domain_event(event_type_input text, schema_version_input integer, aggregate_kind_input text, aggregate_id_input bigint, payload_input jsonb DEFAULT '{}'::jsonb) RETURNS bigint
    LANGUAGE plpgsql
    AS $$
DECLARE
    new_event_id bigint;
BEGIN
    IF event_type_input = '' OR aggregate_kind_input = '' OR schema_version_input < 1 THEN
        RAISE EXCEPTION 'invalid domain event envelope';
    END IF;

    INSERT INTO public.domain_events
        (event_type, schema_version, aggregate_kind, aggregate_id, payload, occurred_at)
    VALUES (
        event_type_input,
        schema_version_input,
        aggregate_kind_input,
        aggregate_id_input,
        coalesce(payload_input, '{}'::jsonb),
        now()
    )
    RETURNING id INTO new_event_id;

    INSERT INTO public.domain_event_deliveries
        (event_id, consumer, available_at, claimed_at, attempts, last_error)
    SELECT new_event_id, consumers.name, now(), NULL, 0, NULL
    FROM public.domain_event_consumers AS consumers;

    -- PostgreSQL delivers NOTIFY only after this transaction commits. It is a
    -- latency hint; durable consumers still poll domain_event_deliveries.
    PERFORM pg_notify('squid_domain_events', new_event_id::text);
    RETURN new_event_id;
END;
$$;


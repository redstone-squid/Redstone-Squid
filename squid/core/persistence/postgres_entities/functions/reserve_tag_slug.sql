CREATE FUNCTION public.reserve_tag_slug() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    automatic boolean := NEW.slug IS NULL;
    base text;
    candidate text;
BEGIN
    IF automatic THEN
        IF TG_OP = 'UPDATE' AND OLD.slug IS NOT NULL THEN
            RAISE EXCEPTION 'A tag slug cannot be cleared' USING ERRCODE = '23502';
        END IF;
        base := trim(both '-' from regexp_replace(lower(coalesce(NEW.query_name, NEW.display_name)), '[^a-z0-9]+', '-', 'g'));
        candidate := coalesce(nullif(base, ''), 'tag');
        base := candidate;
    ELSE
        candidate := NEW.slug;
    END IF;
    LOOP
        INSERT INTO public.tag_slugs (slug, tag_id) VALUES (candidate, NEW.id)
        ON CONFLICT (slug) DO NOTHING;
        IF FOUND OR EXISTS (SELECT 1 FROM public.tag_slugs WHERE slug = candidate AND tag_id = NEW.id) THEN
            NEW.slug := candidate;
            RETURN NEW;
        END IF;
        IF NOT automatic THEN
            RAISE EXCEPTION 'Tag slug % is already reserved', candidate USING ERRCODE = '23505';
        END IF;
        SELECT base || '-' || string_agg(substr('abcdefghijklmnopqrstuvwxyz0123456789', 1 + floor(random()*36)::int, 1), '')
        INTO candidate FROM generate_series(1, 5);
    END LOOP;
END
$$;

"""Reserve stable URL slugs for tag definitions."""

from alembic import op

revision = "812a54bc3f09"
down_revision = "5fa81c27d6b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tag_definitions ADD COLUMN slug text")
    op.execute("""
        CREATE TABLE tag_slugs (
            slug text PRIMARY KEY,
            tag_id bigint REFERENCES tag_definitions(id) ON DELETE SET NULL DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT tag_slugs_owner_key UNIQUE (slug, tag_id),
            CONSTRAINT tag_slugs_format_check CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$')
        )
    """)
    op.execute("COMMENT ON TABLE tag_slugs IS 'Reserved tag URL spellings, retained after retirement.'")
    op.execute("CREATE INDEX tag_slugs_tag_id_idx ON tag_slugs(tag_id)")
    op.execute("""
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
    """)
    op.execute("""
CREATE TRIGGER tag_definitions_reserve_slug
BEFORE INSERT OR UPDATE OF slug ON public.tag_definitions
FOR EACH ROW EXECUTE FUNCTION public.reserve_tag_slug();
    """)
    op.execute("""
        DO $$ DECLARE tag bigint; BEGIN
            FOR tag IN SELECT id FROM tag_definitions ORDER BY query_name IS NULL, id LOOP
                UPDATE tag_definitions SET slug = NULL WHERE id = tag;
            END LOOP;
        END $$;
    """)
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.execute("ALTER TABLE tag_definitions ALTER COLUMN slug SET NOT NULL")
    op.execute("""
        ALTER TABLE tag_definitions ADD CONSTRAINT tag_definitions_slug_owner_fkey
        FOREIGN KEY (slug, id) REFERENCES tag_slugs(slug, tag_id) DEFERRABLE INITIALLY DEFERRED
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER tag_definitions_reserve_slug ON tag_definitions")
    op.execute("DROP FUNCTION public.reserve_tag_slug()")
    op.execute("ALTER TABLE tag_definitions DROP COLUMN slug")
    op.execute("DROP TABLE tag_slugs")

CREATE TABLE public.alembic_migration_history (
    id bigint NOT NULL,
    revision character varying(64) NOT NULL,
    down_revisions character varying(64)[] NOT NULL,
    direction text NOT NULL,
    applied_at timestamp with time zone DEFAULT clock_timestamp() NOT NULL,
    duration_ms integer NOT NULL,
    script_sha256 character(64),
    release_version text NOT NULL,
    applied_from text NOT NULL,
    CONSTRAINT alembic_migration_history_direction_check CHECK ((direction = ANY (ARRAY['upgrade'::text, 'downgrade'::text, 'stamp'::text])))
);

COMMENT ON TABLE public.alembic_migration_history IS 'Append-only history of upgrades, downgrades, and stamps. Entries commit with the migration transaction; stamps record revisions without executing their scripts.';

COMMENT ON COLUMN public.alembic_migration_history.revision IS 'The revision this step moved onto or off of.';

COMMENT ON COLUMN public.alembic_migration_history.down_revisions IS 'Parents of `revision`; more than one only at a branch merge.';

COMMENT ON COLUMN public.alembic_migration_history.direction IS 'upgrade and downgrade ran the script; stamp asserted the revision without running it.';

COMMENT ON COLUMN public.alembic_migration_history.applied_at IS 'clock_timestamp(), not now(): a whole `upgrade head` shares one transaction, so transaction time would stamp every step of a release identically.';

COMMENT ON COLUMN public.alembic_migration_history.duration_ms IS 'Wall time from the previous step of this run. The first row of a run also carries that run''s setup.';

COMMENT ON COLUMN public.alembic_migration_history.script_sha256 IS 'Digest of the revision file for this step, checked while the revision is in the current schema ancestry. NULL when the revision resolved to no file on disk.';

COMMENT ON COLUMN public.alembic_migration_history.release_version IS 'SQUID_BUILD_COMMIT_HASH of the image that ran it.';

COMMENT ON COLUMN public.alembic_migration_history.applied_from IS 'Hostname of the process that ran it, which is the container id under Compose.';

ALTER TABLE ONLY public.alembic_migration_history
    ADD CONSTRAINT alembic_migration_history_pkey PRIMARY KEY (id);


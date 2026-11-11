CREATE TABLE public.starboards (
    id bigint NOT NULL,
    guild_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    name text NOT NULL,
    enabled boolean DEFAULT true NOT NULL,
    required double precision DEFAULT 3.0 NOT NULL,
    required_remove double precision DEFAULT 0.0 NOT NULL,
    self_vote boolean DEFAULT false NOT NULL,
    allow_bots boolean DEFAULT false NOT NULL,
    require_image boolean DEFAULT false NOT NULL,
    min_age_seconds integer DEFAULT 0 NOT NULL,
    max_age_seconds integer DEFAULT 0 NOT NULL,
    autoreact_upvote boolean DEFAULT true NOT NULL,
    autoreact_downvote boolean DEFAULT true NOT NULL,
    remove_invalid_reactions boolean DEFAULT false NOT NULL,
    link_edits boolean DEFAULT true NOT NULL,
    link_deletes boolean DEFAULT true NOT NULL,
    display_emoji text DEFAULT '⭐'::text NOT NULL,
    colour integer DEFAULT 4415105 NOT NULL,
    jump_to_message boolean DEFAULT true NOT NULL,
    attachments_list boolean DEFAULT true NOT NULL,
    replied_to boolean DEFAULT true NOT NULL,
    ping_author boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    revision bigint DEFAULT 1 NOT NULL,
    archived boolean DEFAULT false NOT NULL,
    CONSTRAINT starboards_age_check CHECK (((min_age_seconds >= 0) AND (max_age_seconds >= 0) AND ((max_age_seconds = 0) OR (min_age_seconds <= max_age_seconds)))),
    CONSTRAINT starboards_colour_check CHECK (((colour >= 0) AND (colour <= 16777215))),
    CONSTRAINT starboards_name_check CHECK ((btrim(name) <> ''::text)),
    CONSTRAINT starboards_revision_positive CHECK ((revision > 0)),
    CONSTRAINT starboards_thresholds_check CHECK (((required > required_remove) AND (required <> 'Infinity'::double precision) AND (required <> '-Infinity'::double precision) AND (required <> 'NaN'::double precision) AND (required_remove <> 'Infinity'::double precision) AND (required_remove <> '-Infinity'::double precision) AND (required_remove <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.starboards IS 'A named weighted-message board owned by one Discord guild.';

COMMENT ON COLUMN public.starboards.required IS 'Score at or above which an unposted entry qualifies for publication.';

COMMENT ON COLUMN public.starboards.required_remove IS 'Score at or below which an existing entry no longer qualifies for publication.';

COMMENT ON COLUMN public.starboards.allow_bots IS 'Whether messages authored by bots may qualify; this does not authorize bot voters.';

COMMENT ON COLUMN public.starboards.max_age_seconds IS 'Maximum source-message age in seconds; zero disables the upper age limit.';

ALTER TABLE ONLY public.starboards
    ADD CONSTRAINT starboards_pkey PRIMARY KEY (id);

CREATE UNIQUE INDEX starboards_guild_name_key ON public.starboards USING btree (guild_id, lower(name)) WHERE (NOT archived);

CREATE TRIGGER starboards_enqueue_discord_sync AFTER UPDATE ON public.starboards FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();

ALTER TABLE ONLY public.starboards
    ADD CONSTRAINT starboards_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


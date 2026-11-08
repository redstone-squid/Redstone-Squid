CREATE TABLE public.discord_posts (
    message_id bigint NOT NULL,
    channel_id bigint NOT NULL,
    resource_kind text NOT NULL,
    resource_key text NOT NULL,
    surface text NOT NULL,
    applied_revision bigint DEFAULT 0 NOT NULL,
    posted_at timestamp with time zone DEFAULT now() NOT NULL,
    rendered_at timestamp with time zone,
    suppressed_at timestamp with time zone,
    CONSTRAINT discord_posts_applied_revision_check CHECK ((applied_revision >= 0)),
    CONSTRAINT discord_posts_resource_kind_check CHECK ((resource_kind = ANY (ARRAY['build'::text, 'vote_session'::text, 'starboard_entry'::text, 'inference_run'::text, 'submission_draft'::text]))),
    CONSTRAINT discord_posts_surface_check CHECK ((surface = ANY (ARRAY['build_card'::text, 'build_review'::text, 'vote_card'::text, 'starboard_entry'::text, 'submission_status'::text])))
);

COMMENT ON TABLE public.discord_posts IS 'A bot-owned Discord message displaying a build, vote, or other resource.';

COMMENT ON COLUMN public.discord_posts.channel_id IS 'Copied from the message so the unique index can limit live posts per channel.';

COMMENT ON COLUMN public.discord_posts.resource_kind IS 'The type of resource displayed by this message, used to select its renderer.';

COMMENT ON COLUMN public.discord_posts.resource_key IS 'The resource''s identifier, such as a build ID or a starboard/message ID pair.';

COMMENT ON COLUMN public.discord_posts.surface IS 'The presentation to use, such as a build card or a vote card.';

COMMENT ON COLUMN public.discord_posts.applied_revision IS 'The last rendered queue generation; a larger queued generation means an update is pending.';

COMMENT ON COLUMN public.discord_posts.rendered_at IS 'When a rendered update was last recorded; None until the first update.';

COMMENT ON COLUMN public.discord_posts.suppressed_at IS 'When an external deletion was recorded. Each renderer decides whether to publish a replacement.';

ALTER TABLE ONLY public.discord_posts
    ADD CONSTRAINT discord_posts_pkey PRIMARY KEY (message_id);

CREATE UNIQUE INDEX discord_posts_resource_channel_key ON public.discord_posts USING btree (resource_kind, resource_key, channel_id) WHERE (suppressed_at IS NULL);

CREATE INDEX discord_posts_resource_idx ON public.discord_posts USING btree (resource_kind, resource_key);

ALTER TABLE ONLY public.discord_posts
    ADD CONSTRAINT discord_posts_message_id_fkey FOREIGN KEY (message_id) REFERENCES public.messages(id) ON DELETE RESTRICT;


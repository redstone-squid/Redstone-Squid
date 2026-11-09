CREATE TABLE public.starboard_origin_messages (
    id bigint NOT NULL,
    author_is_bot boolean NOT NULL,
    is_nsfw boolean DEFAULT false NOT NULL,
    has_image boolean DEFAULT false NOT NULL,
    seen_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.starboard_origin_messages IS 'A source message that has been evaluated by at least one starboard.';

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.starboard_origin_messages
    ADD CONSTRAINT starboard_origin_messages_id_fkey FOREIGN KEY (id) REFERENCES public.messages(id) ON DELETE CASCADE;


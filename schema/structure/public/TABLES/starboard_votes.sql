CREATE TABLE public.starboard_votes (
    starboard_id bigint NOT NULL,
    origin_message_id bigint NOT NULL,
    user_id bigint NOT NULL,
    emoji text NOT NULL,
    direction text NOT NULL,
    weight double precision NOT NULL,
    target_author_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT starboard_votes_direction_check CHECK ((direction = ANY (ARRAY['up'::text, 'down'::text]))),
    CONSTRAINT starboard_votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.starboard_votes IS 'One member''s current weighted reaction to one message on one starboard.';

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_pkey PRIMARY KEY (starboard_id, origin_message_id, user_id);

CREATE INDEX starboard_votes_origin_message_idx ON public.starboard_votes USING btree (origin_message_id);

CREATE INDEX starboard_votes_target_author_created_idx ON public.starboard_votes USING btree (starboard_id, target_author_id, created_at);

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_origin_message_id_fkey FOREIGN KEY (origin_message_id) REFERENCES public.starboard_origin_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.starboard_votes
    ADD CONSTRAINT starboard_votes_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


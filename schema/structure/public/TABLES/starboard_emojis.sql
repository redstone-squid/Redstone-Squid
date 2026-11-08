CREATE TABLE public.starboard_emojis (
    starboard_id bigint NOT NULL,
    emoji text NOT NULL,
    direction text NOT NULL,
    multiplier double precision DEFAULT 1.0 NOT NULL,
    "position" smallint NOT NULL,
    CONSTRAINT starboard_emojis_direction_check CHECK ((direction = ANY (ARRAY['up'::text, 'down'::text]))),
    CONSTRAINT starboard_emojis_emoji_check CHECK ((btrim(emoji) <> ''::text)),
    CONSTRAINT starboard_emojis_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision))),
    CONSTRAINT starboard_emojis_position_check CHECK (("position" >= 0))
);

COMMENT ON TABLE public.starboard_emojis IS 'An ordered upvote or downvote emoji for a starboard.';

ALTER TABLE ONLY public.starboard_emojis
    ADD CONSTRAINT starboard_emojis_pkey PRIMARY KEY (starboard_id, emoji);

CREATE UNIQUE INDEX starboard_emojis_position_key ON public.starboard_emojis USING btree (starboard_id, "position");

ALTER TABLE ONLY public.starboard_emojis
    ADD CONSTRAINT starboard_emojis_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


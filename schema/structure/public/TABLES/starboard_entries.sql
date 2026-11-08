CREATE TABLE public.starboard_entries (
    starboard_id bigint NOT NULL,
    origin_message_id bigint NOT NULL,
    score double precision DEFAULT 0.0 NOT NULL,
    raw_count integer DEFAULT 0 NOT NULL,
    last_rendered_score double precision,
    first_posted_at timestamp with time zone,
    updated_at timestamp with time zone
);

COMMENT ON TABLE public.starboard_entries IS 'Current vote totals and rendering bookkeeping for one source message on one starboard.';

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_pkey PRIMARY KEY (starboard_id, origin_message_id);

CREATE INDEX starboard_entries_origin_message_idx ON public.starboard_entries USING btree (origin_message_id);

CREATE INDEX starboard_entries_score_idx ON public.starboard_entries USING btree (starboard_id, score DESC);

CREATE TRIGGER starboard_entries_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE OF score ON public.starboard_entries FOR EACH ROW EXECUTE FUNCTION public.enqueue_starboard_sync();

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_origin_message_id_fkey FOREIGN KEY (origin_message_id) REFERENCES public.starboard_origin_messages(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.starboard_entries
    ADD CONSTRAINT starboard_entries_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


CREATE TABLE public.votes (
    vote_session_id bigint NOT NULL,
    weight double precision NOT NULL,
    guild_id bigint,
    account_id integer NOT NULL,
    option_alias_id bigint NOT NULL,
    CONSTRAINT votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.votes IS 'One account''s selected option and cached positive weight in a session.';

COMMENT ON COLUMN public.votes.weight IS 'Cached positive magnitude; the option choice determines the sign used in threshold scoring.';

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (vote_session_id, account_id);

CREATE INDEX votes_account_idx ON public.votes USING btree (account_id);

CREATE INDEX votes_option_alias_idx ON public.votes USING btree (vote_session_id, option_alias_id);

CREATE TRIGGER votes_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enqueue_discord_sync();

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_option_alias_fkey FOREIGN KEY (vote_session_id, option_alias_id) REFERENCES public.vote_session_options(vote_session_id, id);

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


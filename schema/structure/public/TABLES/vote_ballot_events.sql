CREATE TABLE public.vote_ballot_events (
    id bigint NOT NULL,
    vote_session_id bigint NOT NULL,
    account_id integer NOT NULL,
    change text NOT NULL,
    option_identifier text,
    weight double precision,
    guild_id bigint,
    emoji text,
    recorded_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT vote_ballot_events_change_check CHECK ((change = ANY (ARRAY['cast'::text, 'clear'::text, 'void'::text]))),
    CONSTRAINT vote_ballot_events_selection_check CHECK (((change = 'cast'::text) = ((option_identifier IS NOT NULL) AND (weight IS NOT NULL))))
);

COMMENT ON TABLE public.vote_ballot_events IS 'One change to an account''s ballot, kept after the ballot itself changes again.';

COMMENT ON COLUMN public.vote_ballot_events.option_identifier IS 'The option the ballot selects after a cast, or None after a clear or void.';

COMMENT ON COLUMN public.vote_ballot_events.guild_id IS 'The guild whose card the change came from, or None for a change made without Discord.';

ALTER TABLE ONLY public.vote_ballot_events
    ADD CONSTRAINT vote_ballot_events_pkey PRIMARY KEY (id);

CREATE INDEX vote_ballot_events_account_idx ON public.vote_ballot_events USING btree (account_id);

CREATE INDEX vote_ballot_events_session_idx ON public.vote_ballot_events USING btree (vote_session_id, id);

ALTER TABLE ONLY public.vote_ballot_events
    ADD CONSTRAINT vote_ballot_events_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.vote_ballot_events
    ADD CONSTRAINT vote_ballot_events_option_fkey FOREIGN KEY (vote_session_id, option_identifier) REFERENCES public.vote_session_options(vote_session_id, identifier) ON DELETE CASCADE;

ALTER TABLE ONLY public.vote_ballot_events
    ADD CONSTRAINT vote_ballot_events_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON DELETE CASCADE;


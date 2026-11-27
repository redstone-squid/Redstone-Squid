CREATE TABLE public.votes (
    vote_session_id bigint NOT NULL,
    weight double precision NOT NULL,
    guild_id bigint,
    account_id integer,
    option_identifier text NOT NULL,
    emoji text,
    id bigint NOT NULL,
    discord_user_id bigint,
    CONSTRAINT votes_one_voter_check CHECK ((num_nonnulls(account_id, discord_user_id) = 1)),
    CONSTRAINT votes_weight_check CHECK (((weight > (0)::double precision) AND (weight <> 'Infinity'::double precision) AND (weight <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.votes IS 'One voter''s selected option and the positive weight it was cast with.';

COMMENT ON COLUMN public.votes.weight IS 'Positive magnitude fixed at cast; the option''s choice determines the sign used in threshold scoring.';

COMMENT ON COLUMN public.votes.guild_id IS 'The guild whose card the ballot was cast on, or None for a ballot cast without Discord.';

COMMENT ON COLUMN public.votes.emoji IS 'The alias the ballot was cast with, which a public card keeps as the voter''s reaction.';

COMMENT ON COLUMN public.votes.discord_user_id IS 'The voter''s Discord user ID when they voted through the bot without a consented account.';

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_pkey PRIMARY KEY (id);

CREATE INDEX votes_account_idx ON public.votes USING btree (account_id);

CREATE UNIQUE INDEX votes_session_account_key ON public.votes USING btree (vote_session_id, account_id) WHERE (account_id IS NOT NULL);

CREATE UNIQUE INDEX votes_session_discord_user_key ON public.votes USING btree (vote_session_id, discord_user_id) WHERE (discord_user_id IS NOT NULL);

CREATE TRIGGER votes_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.votes FOR EACH ROW EXECUTE FUNCTION public.enqueue_vote_session_sync();

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_option_fkey FOREIGN KEY (vote_session_id, option_identifier) REFERENCES public.vote_session_options(vote_session_id, identifier);

ALTER TABLE ONLY public.votes
    ADD CONSTRAINT votes_vote_session_id_fkey FOREIGN KEY (vote_session_id) REFERENCES public.vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE;


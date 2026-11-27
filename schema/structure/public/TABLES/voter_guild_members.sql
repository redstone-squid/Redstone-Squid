CREATE TABLE public.voter_guild_members (
    guild_id bigint NOT NULL,
    account_id integer NOT NULL,
    discord_user_id bigint NOT NULL,
    role_ids bigint[] DEFAULT '{}'::bigint[] NOT NULL,
    manage_server boolean DEFAULT false NOT NULL,
    synced_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.voter_guild_members IS 'A consented voter''s roles in one guild, as the bot last saw them.';

COMMENT ON COLUMN public.voter_guild_members.synced_at IS 'When the bot last saw this membership; a complete guild sync forgets older rows.';

ALTER TABLE ONLY public.voter_guild_members
    ADD CONSTRAINT voter_guild_members_pkey PRIMARY KEY (guild_id, account_id);

CREATE INDEX voter_guild_members_discord_user_idx ON public.voter_guild_members USING btree (guild_id, discord_user_id);

ALTER TABLE ONLY public.voter_guild_members
    ADD CONSTRAINT voter_guild_members_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


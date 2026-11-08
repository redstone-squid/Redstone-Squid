CREATE TABLE public.guild_vote_role_weights (
    guild_id bigint NOT NULL,
    kind text NOT NULL,
    role_id bigint NOT NULL,
    multiplier double precision NOT NULL,
    CONSTRAINT guild_vote_role_weights_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text]))),
    CONSTRAINT guild_vote_role_weights_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.guild_vote_role_weights IS 'A role multiplier scoped to one guild and session kind.';

ALTER TABLE ONLY public.guild_vote_role_weights
    ADD CONSTRAINT guild_vote_role_weights_pkey PRIMARY KEY (guild_id, kind, role_id);

ALTER TABLE ONLY public.guild_vote_role_weights
    ADD CONSTRAINT guild_vote_role_weights_guild_id_fkey FOREIGN KEY (guild_id) REFERENCES public.server_settings(server_id) ON DELETE CASCADE;


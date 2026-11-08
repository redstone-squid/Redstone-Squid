CREATE TABLE public.starboard_role_multipliers (
    starboard_id bigint NOT NULL,
    role_id bigint NOT NULL,
    multiplier double precision NOT NULL,
    CONSTRAINT starboard_role_multipliers_multiplier_check CHECK (((multiplier > (0)::double precision) AND (multiplier <> 'Infinity'::double precision) AND (multiplier <> 'NaN'::double precision)))
);

COMMENT ON TABLE public.starboard_role_multipliers IS 'A role multiplier scoped to one starboard.';

ALTER TABLE ONLY public.starboard_role_multipliers
    ADD CONSTRAINT starboard_role_multipliers_pkey PRIMARY KEY (starboard_id, role_id);

ALTER TABLE ONLY public.starboard_role_multipliers
    ADD CONSTRAINT starboard_role_multipliers_starboard_id_fkey FOREIGN KEY (starboard_id) REFERENCES public.starboards(id) ON DELETE CASCADE;


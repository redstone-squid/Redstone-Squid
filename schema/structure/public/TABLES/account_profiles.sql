CREATE TABLE public.account_profiles (
    account_id integer NOT NULL,
    display_name text,
    bio text,
    pronouns text,
    links jsonb DEFAULT '[]'::jsonb NOT NULL,
    hidden boolean DEFAULT false NOT NULL,
    avatar_identity_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT account_profiles_bio_length CHECK (((bio IS NULL) OR ((char_length(bio) >= 1) AND (char_length(bio) <= 500)))),
    CONSTRAINT account_profiles_display_name_length CHECK (((display_name IS NULL) OR ((char_length(display_name) >= 1) AND (char_length(display_name) <= 64)))),
    CONSTRAINT account_profiles_display_name_trimmed CHECK (((display_name IS NULL) OR (display_name = btrim(display_name)))),
    CONSTRAINT account_profiles_links_shape CHECK (((jsonb_typeof(links) = 'array'::text) AND (jsonb_array_length(links) <= 10))),
    CONSTRAINT account_profiles_pronouns_length CHECK (((pronouns IS NULL) OR ((char_length(pronouns) >= 1) AND (char_length(pronouns) <= 40))))
);

COMMENT ON TABLE public.account_profiles IS 'Public profile content and visibility choices for an account.';

COMMENT ON COLUMN public.account_profiles.display_name IS 'Public display name, independent of the creator aliases used for build credits.';

COMMENT ON COLUMN public.account_profiles.links IS 'Published links, stored as a JSON array. Replace the tuple when editing links.';

COMMENT ON COLUMN public.account_profiles.hidden IS 'Whether to hide profile content. Aliases and build credits remain public.';

COMMENT ON COLUMN public.account_profiles.avatar_identity_id IS 'Linked identity used for the avatar. Unlinking it clears this choice; writes check account ownership.';

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_pkey PRIMARY KEY (account_id);

CREATE INDEX account_profiles_avatar_identity_idx ON public.account_profiles USING btree (avatar_identity_id);

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.account_profiles
    ADD CONSTRAINT account_profiles_avatar_identity_id_fkey FOREIGN KEY (avatar_identity_id) REFERENCES public.account_identities(id) ON DELETE SET NULL;


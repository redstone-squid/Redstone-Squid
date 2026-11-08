CREATE TABLE public.minecraft_paper_installations (
    id uuid NOT NULL,
    owner_account_id integer NOT NULL,
    label text NOT NULL,
    secret_hash bytea NOT NULL,
    credential_version integer DEFAULT 1 NOT NULL,
    public_profile_enabled boolean DEFAULT false NOT NULL,
    public_display_name text,
    public_address text,
    public_description text,
    public_website_url text,
    sponsor_opt_in boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    rotated_at timestamp with time zone,
    revoked_at timestamp with time zone,
    CONSTRAINT minecraft_paper_installations_address_length CHECK (((public_address IS NULL) OR ((char_length(public_address) >= 1) AND (char_length(public_address) <= 255)))),
    CONSTRAINT minecraft_paper_installations_description_length CHECK (((public_description IS NULL) OR ((char_length(public_description) >= 1) AND (char_length(public_description) <= 500)))),
    CONSTRAINT minecraft_paper_installations_display_name_length CHECK (((public_display_name IS NULL) OR ((char_length(public_display_name) >= 1) AND (char_length(public_display_name) <= 80)))),
    CONSTRAINT minecraft_paper_installations_label_length CHECK (((char_length(label) >= 1) AND (char_length(label) <= 80))),
    CONSTRAINT minecraft_paper_installations_secret_hash_length CHECK ((octet_length(secret_hash) = 32)),
    CONSTRAINT minecraft_paper_installations_sponsor_requires_public CHECK (((NOT sponsor_opt_in) OR public_profile_enabled)),
    CONSTRAINT minecraft_paper_installations_version_positive CHECK ((credential_version >= 1)),
    CONSTRAINT minecraft_paper_installations_website_length CHECK (((public_website_url IS NULL) OR ((char_length(public_website_url) >= 1) AND (char_length(public_website_url) <= 2048))))
);

COMMENT ON TABLE public.minecraft_paper_installations IS 'An account-owned Paper installation with a non-recoverable credential.';

ALTER TABLE ONLY public.minecraft_paper_installations
    ADD CONSTRAINT minecraft_paper_installations_pkey PRIMARY KEY (id);

CREATE INDEX minecraft_paper_installations_owner_idx ON public.minecraft_paper_installations USING btree (owner_account_id, created_at);

CREATE INDEX minecraft_paper_installations_public_idx ON public.minecraft_paper_installations USING btree (created_at) WHERE (public_profile_enabled AND (revoked_at IS NULL));

ALTER TABLE ONLY public.minecraft_paper_installations
    ADD CONSTRAINT minecraft_paper_installations_owner_account_id_fkey FOREIGN KEY (owner_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


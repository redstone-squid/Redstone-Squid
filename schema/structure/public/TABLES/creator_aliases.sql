CREATE TABLE public.creator_aliases (
    id integer NOT NULL,
    name text NOT NULL,
    normalized_name text NOT NULL,
    account_id integer,
    claimed_at timestamp with time zone,
    claim_method text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT creator_aliases_claim_complete CHECK (((account_id IS NULL) = (claimed_at IS NULL))),
    CONSTRAINT creator_aliases_claim_method_check CHECK (((claim_method IS NULL) OR (claim_method = ANY (ARRAY['verified_ign'::text, 'staff_approved'::text, 'migrated'::text])))),
    CONSTRAINT creator_aliases_claim_method_complete CHECK (((account_id IS NULL) = (claim_method IS NULL))),
    CONSTRAINT creator_aliases_normalized_name_folded CHECK (((normalized_name = btrim(normalized_name)) AND (normalized_name !~ '[A-Z]'::text)))
);

COMMENT ON TABLE public.creator_aliases IS 'A creator name credited on a build, optionally claimed by an account.';

ALTER TABLE ONLY public.creator_aliases ALTER COLUMN id SET DEFAULT nextval('public.creator_aliases_id_seq'::regclass);

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_normalized_name_key UNIQUE (normalized_name);

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_pkey PRIMARY KEY (id);

CREATE INDEX creator_aliases_account_idx ON public.creator_aliases USING btree (account_id);

CREATE INDEX creator_aliases_normalized_name_prefix_idx ON public.creator_aliases USING btree (normalized_name text_pattern_ops);

CREATE TRIGGER creator_aliases_enqueue_search AFTER INSERT OR DELETE OR UPDATE ON public.creator_aliases FOR EACH ROW EXECUTE FUNCTION public.enqueue_metadata_search_projection();

ALTER TABLE ONLY public.creator_aliases
    ADD CONSTRAINT creator_aliases_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


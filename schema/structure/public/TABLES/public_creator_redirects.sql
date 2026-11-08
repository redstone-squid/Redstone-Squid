CREATE TABLE public.public_creator_redirects (
    retired_public_creator_id uuid NOT NULL,
    target_account_id integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);

COMMENT ON TABLE public.public_creator_redirects IS 'Permanent redirect from a merged public creator identifier.';

ALTER TABLE ONLY public.public_creator_redirects
    ADD CONSTRAINT public_creator_redirects_pkey PRIMARY KEY (retired_public_creator_id);

CREATE INDEX public_creator_redirects_target_idx ON public.public_creator_redirects USING btree (target_account_id);

ALTER TABLE ONLY public.public_creator_redirects
    ADD CONSTRAINT public_creator_redirects_target_account_id_fkey FOREIGN KEY (target_account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


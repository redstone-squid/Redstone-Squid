CREATE TABLE public.account_merge_tickets (
    account_id integer NOT NULL,
    code_digest text NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT account_merge_tickets_expiry_after_creation CHECK ((expires_at > created_at))
);

COMMENT ON TABLE public.account_merge_tickets IS 'A single-use proof that an account consents to being merged into another account.';

COMMENT ON COLUMN public.account_merge_tickets.code_digest IS 'Digest of the code shown once when the ticket is minted.';

COMMENT ON COLUMN public.account_merge_tickets.expires_at IS 'End of the authentication proof window in which the ticket can be redeemed.';

ALTER TABLE ONLY public.account_merge_tickets
    ADD CONSTRAINT account_merge_tickets_pkey PRIMARY KEY (account_id);

CREATE INDEX account_merge_tickets_code_digest_idx ON public.account_merge_tickets USING btree (code_digest);

ALTER TABLE ONLY public.account_merge_tickets
    ADD CONSTRAINT account_merge_tickets_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


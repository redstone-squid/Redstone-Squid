CREATE TABLE public.build_credits (
    build_id bigint NOT NULL,
    "position" smallint NOT NULL,
    name text NOT NULL,
    kind text NOT NULL,
    username text,
    subject text,
    role text NOT NULL,
    attribution text NOT NULL,
    source_message_id bigint,
    alias_id integer,
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    account_id integer,
    proposed_account_id integer,
    account_source text DEFAULT 'automatic'::text NOT NULL,
    CONSTRAINT build_credits_account_source_check CHECK ((account_source = ANY (ARRAY['automatic'::text, 'reviewed'::text, 'conflict'::text]))),
    CONSTRAINT build_credits_attribution_valid CHECK ((attribution = ANY (ARRAY['explicit'::text, 'assumed_sender'::text, 'confirmed'::text, 'legacy'::text]))),
    CONSTRAINT build_credits_generic_reference CHECK (((kind <> 'name'::text) OR ((subject IS NULL) AND (username IS NULL)))),
    CONSTRAINT build_credits_kind_valid CHECK ((kind = ANY (ARRAY['name'::text, 'discord'::text, 'minecraft_java'::text, 'minecraft_bedrock'::text]))),
    CONSTRAINT build_credits_name_valid CHECK (((name = btrim(name)) AND ((char_length(name) >= 1) AND (char_length(name) <= 255)))),
    CONSTRAINT build_credits_position_nonnegative CHECK (("position" >= 0)),
    CONSTRAINT build_credits_role_valid CHECK ((role = ANY (ARRAY['creator'::text, 'designer'::text, 'layout'::text, 'contributor'::text])))
);

COMMENT ON TABLE public.build_credits IS 'A build contribution and the observed identity used to attribute it.';

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_pkey PRIMARY KEY (id);

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_position_key UNIQUE (build_id, "position") DEFERRABLE INITIALLY DEFERRED;

CREATE INDEX build_credits_alias_idx ON public.build_credits USING btree (alias_id);

CREATE INDEX build_credits_subject_idx ON public.build_credits USING btree (kind, subject);

CREATE INDEX ix_build_credits_account_id ON public.build_credits USING btree (account_id);

CREATE INDEX ix_build_credits_proposed_account_id ON public.build_credits USING btree (proposed_account_id);

CREATE TRIGGER build_credits_capture_edit BEFORE INSERT OR DELETE OR UPDATE ON public.build_credits FOR EACH ROW EXECUTE FUNCTION public.capture_build_edit_trigger();

CREATE TRIGGER build_credits_enqueue_build_change AFTER INSERT OR DELETE OR UPDATE ON public.build_credits FOR EACH ROW EXECUTE FUNCTION public.enqueue_build_change();

CREATE TRIGGER build_credits_reject_truncate BEFORE TRUNCATE ON public.build_credits FOR EACH STATEMENT EXECUTE FUNCTION public.capture_build_edit_trigger();

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_account_id_fkey FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_alias_id_fkey FOREIGN KEY (alias_id) REFERENCES public.creator_aliases(id) ON DELETE RESTRICT;

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_build_id_fkey FOREIGN KEY (build_id) REFERENCES public.builds(id) ON DELETE CASCADE;

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_proposed_account_id_fkey FOREIGN KEY (proposed_account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;

ALTER TABLE ONLY public.build_credits
    ADD CONSTRAINT build_credits_source_message_id_fkey FOREIGN KEY (source_message_id) REFERENCES public.messages(id) ON DELETE RESTRICT;


CREATE TABLE public.vote_sessions (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    status text NOT NULL,
    kind text NOT NULL,
    fail_threshold smallint,
    pass_threshold smallint,
    result text DEFAULT 'pending'::text NOT NULL,
    author_account_id integer NOT NULL,
    CONSTRAINT vote_sessions_kind_check CHECK ((kind = ANY (ARRAY['build'::text, 'delete_log'::text, 'generic'::text]))),
    CONSTRAINT vote_sessions_result_check CHECK ((result = ANY (ARRAY['approved'::text, 'denied'::text, 'cancelled'::text, 'pending'::text]))),
    CONSTRAINT vote_sessions_status_check CHECK ((status = ANY (ARRAY['open'::text, 'closed'::text]))),
    CONSTRAINT vote_sessions_threshold_kind_check CHECK (
CASE
    WHEN (kind = 'generic'::text) THEN ((pass_threshold IS NULL) AND (fail_threshold IS NULL))
    ELSE ((pass_threshold IS NOT NULL) AND (fail_threshold IS NOT NULL) AND (pass_threshold > 0) AND (fail_threshold < 0))
END)
);

COMMENT ON TABLE public.vote_sessions IS 'A voting session for builds, log deletions, or generic polls.';

COMMENT ON COLUMN public.vote_sessions.fail_threshold IS 'Negative net score required for denial, or None for a generic poll.';

COMMENT ON COLUMN public.vote_sessions.pass_threshold IS 'Positive net score required for approval, or None for a generic poll.';

COMMENT ON COLUMN public.vote_sessions.result IS 'Pending until closure; threshold outcomes are approved or denied, and other closures are cancelled.';

ALTER TABLE ONLY public.vote_sessions
    ADD CONSTRAINT vote_sessions_pkey PRIMARY KEY (id);

CREATE INDEX vote_sessions_author_idx ON public.vote_sessions USING btree (author_account_id);

CREATE TRIGGER vote_sessions_emit_domain_event AFTER UPDATE OF status ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.emit_domain_event();

CREATE TRIGGER vote_sessions_enqueue_discord_sync AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions FOR EACH ROW EXECUTE FUNCTION public.enqueue_vote_session_sync();

CREATE CONSTRAINT TRIGGER vote_sessions_kind_subtype_check AFTER INSERT OR DELETE OR UPDATE ON public.vote_sessions DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.enforce_vote_session_kind_subtype();

ALTER TABLE ONLY public.vote_sessions
    ADD CONSTRAINT vote_sessions_author_account_id_fkey FOREIGN KEY (author_account_id) REFERENCES public.accounts(id) ON DELETE RESTRICT;


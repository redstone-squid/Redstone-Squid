CREATE TABLE public.build_edit_pending (
    transaction_id bigint NOT NULL,
    build_id bigint NOT NULL,
    context jsonb NOT NULL,
    before_snapshot jsonb,
    before_revision bigint,
    CONSTRAINT build_edit_pending_before_check CHECK (((before_snapshot IS NULL) = (before_revision IS NULL)))
);

COMMENT ON TABLE public.build_edit_pending IS 'Transaction-local work removed by the deferred history trigger before commit.';

ALTER TABLE ONLY public.build_edit_pending
    ADD CONSTRAINT build_edit_pending_pkey PRIMARY KEY (transaction_id, build_id);

CREATE CONSTRAINT TRIGGER build_edit_finalize AFTER INSERT ON public.build_edit_pending DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.finish_build_edit_trigger();


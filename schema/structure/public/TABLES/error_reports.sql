CREATE TABLE public.error_reports (
    id uuid NOT NULL,
    correlation_id text NOT NULL,
    reference text NOT NULL,
    occurred_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    surface text NOT NULL,
    origin text,
    exception_type text NOT NULL,
    message text NOT NULL,
    error_code text,
    traceback text NOT NULL,
    context jsonb NOT NULL,
    log_tail jsonb NOT NULL,
    work_lost boolean DEFAULT false NOT NULL
);

COMMENT ON TABLE public.error_reports IS 'One unexpected failure, retained so the reference its user was shown can be resolved.';

COMMENT ON COLUMN public.error_reports.correlation_id IS 'The full correlation ID, as it appears in logs and the Request-Id response header.';

COMMENT ON COLUMN public.error_reports.reference IS 'Short reference shown to the user, usually the first 12 correlation-ID characters.

References are not unique. Multiple failures can share a correlation ID, and
different IDs can share a prefix; lookup also returns a match count.';

COMMENT ON COLUMN public.error_reports.occurred_at IS 'When the failure was captured.';

COMMENT ON COLUMN public.error_reports.expires_at IS 'When the report stops appearing in lookups and becomes eligible for deletion.';

COMMENT ON COLUMN public.error_reports.surface IS 'Which transport failed: an application command, a view callback, a route, a worker job.';

COMMENT ON COLUMN public.error_reports.origin IS 'The command name, route, or job the failure came from, when the surface knows it.';

COMMENT ON COLUMN public.error_reports.exception_type IS 'Qualified name of the raised exception class.';

COMMENT ON COLUMN public.error_reports.message IS 'The exception''s string form, available to callers authorized to read diagnostic details.';

COMMENT ON COLUMN public.error_reports.error_code IS 'The application ErrorCode, when the failure carried one.';

COMMENT ON COLUMN public.error_reports.traceback IS 'Rendered traceback, truncated from the front so the frames nearest the failure survive.';

COMMENT ON COLUMN public.error_reports.context IS 'Diagnostic context supplied by the caller; the storage service does not redact it.';

COMMENT ON COLUMN public.error_reports.log_tail IS 'What the process logged under this correlation ID before failing, oldest first.';

COMMENT ON COLUMN public.error_reports.work_lost IS 'Whether this failure permanently abandoned work, as a dead-lettered job does.';

ALTER TABLE ONLY public.error_reports
    ADD CONSTRAINT error_reports_pkey PRIMARY KEY (id);

CREATE INDEX error_reports_correlation_id_idx ON public.error_reports USING btree (correlation_id);

CREATE INDEX error_reports_expires_at_idx ON public.error_reports USING btree (expires_at);

CREATE INDEX error_reports_occurred_at_idx ON public.error_reports USING btree (occurred_at);

CREATE INDEX error_reports_reference_idx ON public.error_reports USING btree (reference);

CREATE INDEX error_reports_work_lost_idx ON public.error_reports USING btree (occurred_at) WHERE work_lost;


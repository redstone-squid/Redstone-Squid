CREATE TABLE
  public.builds (
    id bigint NOT NULL DEFAULT nextval('submissions_submission_id_seq'::regclass),
    submission_status smallint NOT NULL DEFAULT NULL,
    edited_time timestamp with time zone NULL DEFAULT (now() AT TIME ZONE 'utc'::text),
    record_category text NULL DEFAULT NULL,
    extra_info jsonb NOT NULL DEFAULT '{}'::jsonb,
    width integer NULL DEFAULT NULL,
    height integer NULL DEFAULT NULL,
    depth integer NULL DEFAULT NULL,
    completion_time text NULL DEFAULT NULL,
    submission_time timestamp without time zone NULL DEFAULT CURRENT_TIMESTAMP,
    category text NULL DEFAULT NULL,
    submitter_id bigint NOT NULL DEFAULT NULL,
    ai_generated boolean NOT NULL DEFAULT NULL,
    original_message_id bigint NULL DEFAULT NULL,
    version_spec text NULL DEFAULT NULL,
    embedding USER - DEFINED NULL DEFAULT NULL,
    is_locked boolean NOT NULL DEFAULT false,
    locked_at timestamp with time zone NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX submissions_pkey ON public.builds USING btree (id);

CREATE INDEX idx_builds_category ON public.builds USING btree (category)
WHERE
  (category IS NOT NULL);

CREATE INDEX idx_builds_record_category ON public.builds USING btree (record_category)
WHERE
  (record_category IS NOT NULL);

CREATE INDEX idx_builds_submission_time ON public.builds USING btree (submission_time DESC);
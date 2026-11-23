-- Preview by default. Pass cutoff as the UTC deployment time of the media fix.
-- Execute one batch with -v execute=on. Resume with -v after_id=<next_after_id>.
-- Use -v only_ids=1574,10133,10134 to select a canary.
\set ON_ERROR_STOP on
\if :{?execute}
\else
    \set execute off
\endif
\if :{?after_id}
\else
    \set after_id 0
\endif
\if :{?only_ids}
\else
    \set only_ids ''
\endif
\if :{?batch_size}
\else
    \set batch_size 100
\endif

\if :execute
    BEGIN;
    \set queue_cte ', queued AS (INSERT INTO public.discord_sync_queue (resource_kind, source_key, action) SELECT ''build'', build_id::text, ''refresh'' FROM batch ON CONFLICT (resource_kind, source_key) DO NOTHING RETURNING source_key)'
    \set queued_count '(SELECT count(*) FROM queued)'
\else
    BEGIN READ ONLY;
    \set queue_cte ''
    \set queued_count '0'
\endif

WITH retained AS (
    SELECT DISTINCT b.id AS build_id
    FROM public.builds b
    JOIN public.submission_finalization_results r ON r.build_id = b.id
    JOIN public.submission_receipt_media rm ON rm.job_id = r.job_id
    JOIN public.media_uploads u ON u.id = rm.upload_id AND u.draft_id = b.source_submission_draft_id
    JOIN public.media_artifacts a ON a.upload_id = u.id
    JOIN public.media_artifact_objects o ON o.object_key = a.object_key
    WHERE b.submission_status = 1
      AND a.role = 'output'
      AND a.content_type IN ('image/png', 'image/apng', 'image/jpeg', 'image/gif', 'image/webp',
                             'video/mp4', 'video/quicktime', 'video/webm', 'video/x-matroska',
                             'video/x-msvideo', 'video/mpeg')
      AND o.deleted_at IS NULL
      AND b.id > :'after_id'::bigint
      AND (:'only_ids' = '' OR b.id = ANY(string_to_array(:'only_ids', ',')::bigint[]))
), stale AS (
    SELECT r.build_id, count(*) AS cards
    FROM retained r
    JOIN public.discord_posts p ON p.resource_kind = 'build' AND p.resource_key = r.build_id::text
    JOIN public.messages m ON m.id = p.message_id
    WHERE p.surface = 'build_card' AND p.suppressed_at IS NULL AND m.deleted_at IS NULL
      AND coalesce(p.rendered_at, p.posted_at) < :'cutoff'::timestamptz
    GROUP BY r.build_id
), available AS (
    SELECT s.*
    FROM stale s
    WHERE NOT EXISTS (
        SELECT FROM public.discord_sync_queue q WHERE q.resource_kind = 'build' AND q.source_key = s.build_id::text
    )
), batch AS (
    SELECT * FROM available ORDER BY build_id LIMIT least(greatest(:'batch_size'::int, 1), 500)
)
:queue_cte
SELECT jsonb_build_object(
    'mode', CASE WHEN :'execute'::boolean THEN 'execute' ELSE 'dry-run' END,
    'cutoff', :'cutoff'::timestamptz,
    'stale_builds', (SELECT count(*) FROM stale),
    'stale_cards', (SELECT coalesce(sum(cards), 0) FROM stale),
    'builds_with_existing_queue_entry', (SELECT count(*) FROM stale) - (SELECT count(*) FROM available),
    'eligible_builds', (SELECT count(*) FROM available),
    'eligible_cards', (SELECT coalesce(sum(cards), 0) FROM available),
    'batch_builds', (SELECT count(*) FROM batch),
    'sample_build_ids', (SELECT coalesce(jsonb_agg(build_id ORDER BY build_id), '[]') FROM (
        SELECT build_id FROM batch ORDER BY build_id LIMIT 10
    ) sample),
    'queued_builds', :queued_count,
    'next_after_id', (SELECT max(build_id) FROM batch)
);
COMMIT;

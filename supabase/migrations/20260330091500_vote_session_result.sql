BEGIN;

ALTER TABLE public.vote_sessions
ADD COLUMN result text;

UPDATE public.vote_sessions AS vote_sessions
SET result = CASE
    WHEN vote_sessions.status = 'open' THEN 'pending'
    WHEN COALESCE(vote_totals.net_votes, 0) >= vote_sessions.pass_threshold THEN 'approved'
    WHEN COALESCE(vote_totals.net_votes, 0) <= vote_sessions.fail_threshold THEN 'denied'
    ELSE 'cancelled'
END
FROM (
    SELECT vote_session_id, COALESCE(SUM(weight), 0) AS net_votes
    FROM public.votes
    GROUP BY vote_session_id
) AS vote_totals
WHERE vote_sessions.id = vote_totals.vote_session_id;

UPDATE public.vote_sessions
SET result = CASE
    WHEN status = 'open' THEN 'pending'
    ELSE 'cancelled'
END
WHERE result IS NULL;

ALTER TABLE public.vote_sessions
ALTER COLUMN result SET DEFAULT 'pending',
ALTER COLUMN result SET NOT NULL;

ALTER TABLE public.vote_sessions
ADD CONSTRAINT vote_sessions_result_check
CHECK (result IN ('approved', 'denied', 'cancelled', 'pending')) NOT VALID;

ALTER TABLE public.vote_sessions
VALIDATE CONSTRAINT vote_sessions_result_check;

COMMENT ON COLUMN public.vote_sessions.result IS 'The result of the vote session.';

COMMIT;

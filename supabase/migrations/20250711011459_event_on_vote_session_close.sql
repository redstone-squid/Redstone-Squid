BEGIN;

ALTER TABLE vote_sessions
ADD COLUMN result TEXT DEFAULT 'pending' NOT NULL;

ALTER TABLE vote_sessions
ADD CONSTRAINT chk_vote_session_result
CHECK (result IN ('approved', 'rejected', 'pending', 'cancelled'));

COMMENT ON COLUMN vote_sessions.result IS 'The result of the vote session.';

COMMIT;
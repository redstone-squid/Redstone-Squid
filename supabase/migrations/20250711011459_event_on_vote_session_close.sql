BEGIN;

ALTER TABLE vote_sessions
ADD COLUMN result TEXT DEFAULT 'pending' NOT NULL;

ALTER TABLE vote_sessions
ADD CONSTRAINT chk_vote_session_result
CHECK (result IN ('approved', 'rejected', 'pending', 'cancelled'));

COMMENT ON COLUMN vote_sessions.result IS 'The result of the vote session.';

CREATE OR REPLACE FUNCTION trg_vote_session_close_f()
RETURNS TRIGGER
LANGUAGE plpgsql AS
$$
BEGIN
    IF OLD.status = 'open'
       AND NEW.status = 'closed'
       AND NEW.result IN ('approved','denied','cancelled') THEN

        INSERT INTO event_outbox (aggregate, aggregate_id, type, payload)
        VALUES ('vote_session',
                NEW.id,
                'vote_session_closed',
                jsonb_build_object(
                    'result',    NEW.result,
                    'closed_at', NOW()
                ));
    END IF;

    RETURN NEW;  -- required for AFTER-ROW triggers
END;
$$;

-- 2. Trigger declaration
CREATE TRIGGER trg_vote_session_close
AFTER UPDATE OF status ON vote_sessions
FOR EACH ROW
EXECUTE FUNCTION trg_vote_session_close_f();


COMMIT;
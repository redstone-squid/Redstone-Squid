BEGIN;

CREATE TABLE vote_session_emojis (
    vote_session_id BIGINT NOT NULL
        REFERENCES vote_sessions(id) ON DELETE CASCADE,
    emoji        TEXT NOT NULL,
    default_multiplier      NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (vote_session_id, emoji)
);

COMMENT ON COLUMN vote_session_emojis.default_multiplier IS 'The base multiplier for this emoji in the vote session. Only a suggestion, application logic may override this';

CREATE INDEX idx_vote_session_emojis_session
    ON vote_session_emojis (vote_session_id);

ALTER TABLE votes
    ADD COLUMN emoji TEXT;

COMMIT;
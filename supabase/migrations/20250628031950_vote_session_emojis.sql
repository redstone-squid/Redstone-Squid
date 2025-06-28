BEGIN;

CREATE TABLE emojis (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT UNIQUE NOT NULL              -- e.g. '👍' or '<:blobcat:123…>'
);

COMMENT ON COLUMN emojis.symbol IS 'The emoji symbol, either a Unicode character or a custom discord emoji in the format <:name:id>';

CREATE TABLE vote_session_emojis (
    vote_session_id BIGINT NOT NULL
        REFERENCES vote_sessions(id) ON DELETE CASCADE,
    emoji_id        INTEGER NOT NULL
        REFERENCES emojis(id),
    default_multiplier      NUMERIC NOT NULL DEFAULT 1.0,
    PRIMARY KEY (vote_session_id, emoji_id)
);

COMMENT ON COLUMN vote_session_emojis.default_multiplier IS 'The base multiplier for this emoji in the vote session. Only a suggestion, application logic may override this';

CREATE INDEX idx_vote_session_emojis_session
    ON vote_session_emojis (vote_session_id);

COMMIT;
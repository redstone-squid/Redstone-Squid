-- Add indexes on commonly queried fields
CREATE INDEX idx_builds_submission_time ON builds(submission_time DESC);
CREATE INDEX idx_builds_category ON builds(category) WHERE category IS NOT NULL;
CREATE INDEX idx_builds_record_category ON builds(record_category) WHERE record_category IS NOT NULL;

-- Rename edited_time to updated_at
ALTER TABLE messages RENAME COLUMN edited_time TO updated_at;

-- Create function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Add trigger to update updated_at
CREATE TRIGGER update_messages_updated_at
    BEFORE UPDATE ON messages
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Add author_id to messages
ALTER TABLE messages ADD COLUMN author_id bigint;

-- Convert "server_info" jsonb column into a subkey in "information" jsonb
UPDATE builds
SET information = information || ('{"server_info":' || server_info::text || '}')::jsonb
WHERE server_info IS NOT NULL
-- Check for empty server_info
AND server_info <> '{}';

-- Drop the server_info column
ALTER TABLE builds DROP COLUMN server_info;

ALTER TABLE builds rename column information to extra_info;

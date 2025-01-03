UPDATE messages
SET purpose = 'build_post'
WHERE purpose IS NULL;

ALTER TABLE messages
ALTER COLUMN purpose SET NOT NULL;
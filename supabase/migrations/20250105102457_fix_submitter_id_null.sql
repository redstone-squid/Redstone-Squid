UPDATE builds
SET submitter_id = 353089661175988224
WHERE submitter_id IS NULL;

ALTER TABLE builds
ALTER COLUMN submitter_id SET NOT NULL;
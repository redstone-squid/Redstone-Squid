CREATE TABLE verification_codes (
  id SMALLSERIAL PRIMARY KEY,
  minecraft_uuid UUID NOT NULL,
  code TEXT NOT NULL,
  created TIMESTAMP DEFAULT now() NOT NULL,
  expires TIMESTAMP DEFAULT now() + INTERVAL '10 minutes' NOT NULL
);

ALTER TABLE users ALTER COLUMN created_at SET DATA TYPE timestamp;
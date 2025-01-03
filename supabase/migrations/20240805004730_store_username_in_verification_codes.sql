ALTER TABLE verification_codes ADD COLUMN username TEXT NOT NULL default '';
ALTER TABLE verification_codes ADD COLUMN valid BOOLEAN NOT NULL default true;
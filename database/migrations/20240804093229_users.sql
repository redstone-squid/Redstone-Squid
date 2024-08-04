CREATE TABLE users (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT,
    minecraft_uuid UUID,
    ign TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

INSERT INTO users(ign)
select build_creators.creator_ign
from build_creators
where build_creators.creator_ign not in (select ign from users);

ALTER TABLE build_creators ADD COLUMN user_id INT REFERENCES users(id);

UPDATE build_creators
SET user_id = users.id
FROM users
WHERE build_creators.creator_ign = users.ign;

ALTER TABLE build_creators DROP COLUMN creator_ign;
ALTER TABLE build_creators ADD PRIMARY KEY (build_id, user_id);

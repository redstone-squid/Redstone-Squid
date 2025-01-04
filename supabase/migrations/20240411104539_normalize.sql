ALTER TABLE builds ADD COLUMN category TEXT;

update builds
set category = 'Door'
where 1=1;

ALTER TABLE builds RENAME COLUMN build_width TO width;
ALTER TABLE builds RENAME COLUMN build_height TO height;
ALTER TABLE builds RENAME COLUMN build_depth TO depth;
ALTER TABLE builds RENAME COLUMN date_of_creation TO completion_time;
ALTER TABLE builds RENAME COLUMN last_update TO edited_time;

ALTER TABLE builds ALTER COLUMN information SET DATA TYPE JSONB using to_jsonb(information);

update builds
set information = ('{"user":' || information::text || '}')::jsonb
where 1=1;

update builds
set information = ('{}')::jsonb
where information is null;

ALTER TABLE builds ADD COLUMN server_info JSONB;
ALTER TABLE builds DROP COLUMN server_ip;
ALTER TABLE builds DROP COLUMN coordinates;
ALTER TABLE builds DROP COLUMN command_to_build;
ALTER TABLE builds DROP COLUMN locationality;
ALTER TABLE builds DROP COLUMN directionality;

ALTER TABLE builds ADD COLUMN submitter_id BIGINT;
ALTER TABLE builds DROP COLUMN submitted_by;

ALTER TABLE messages ADD COLUMN purpose TEXT;
ALTER TABLE messages RENAME COLUMN last_updated to edited_time;

CREATE TABLE doors (
  build_id BIGINT PRIMARY KEY,
  orientation TEXT not null,
  door_width INT not null,
  door_height INT not null,
  door_depth INT,
  normal_opening_time BIGINT,
  normal_closing_time BIGINT,
  visible_opening_time BIGINT,
  visible_closing_time BIGINT,
  FOREIGN KEY (build_id) REFERENCES builds(id)
);

insert into doors(build_id, orientation, door_width, door_height, normal_opening_time, normal_closing_time, visible_opening_time, visible_closing_time)
select id, door_type, door_width, door_height, normal_opening_time, normal_closing_time, visible_opening_time, visible_closing_time
from builds
where 1=1;
ALTER TABLE builds DROP COLUMN door_type;
ALTER TABLE builds DROP COLUMN door_width;
ALTER TABLE builds DROP COLUMN door_height;
ALTER TABLE builds DROP COLUMN normal_opening_time;
ALTER TABLE builds DROP COLUMN normal_closing_time;
ALTER TABLE builds DROP COLUMN visible_opening_time;
ALTER TABLE builds DROP COLUMN visible_closing_time;

CREATE TABLE extenders (
  build_id BIGINT PRIMARY KEY REFERENCES builds(id)
);

CREATE TABLE entrances (
  build_id BIGINT PRIMARY KEY REFERENCES builds(id)
);

CREATE TABLE utilities (
  build_id BIGINT PRIMARY KEY REFERENCES builds(id)
);

CREATE TABLE build_creators (
  build_id BIGINT,
  creator_ign TEXT,
  PRIMARY KEY (build_id, creator_ign),
  FOREIGN KEY (build_id) REFERENCES builds(id)
);
insert into build_creators(build_id, creator_ign)
select id, creators_ign  -- creator_ign is comma separated list in the builds table, will need manual cleanup
from builds
where creators_ign is not null;
ALTER TABLE builds DROP COLUMN creators_ign;

CREATE TABLE restrictions (
  id SMALLSERIAL PRIMARY KEY,
  build_category TEXT,
  name TEXT UNIQUE,
  type TEXT
);

CREATE TABLE build_restrictions (
  build_id BIGINT,
  restriction_id SMALLINT,
  PRIMARY KEY (build_id, restriction_id),
  FOREIGN KEY (build_id) REFERENCES builds(id),
  FOREIGN KEY (restriction_id) REFERENCES restrictions(id)
);

insert into build_restrictions
select builds.id, restrictions.id
from builds, restrictions
where upper(wiring_placement_restrictions) = upper(restrictions.name);

update builds
SET information = jsonb_set(
    information,
    '{unknown_restrictions}',
    coalesce(information->'unknown_restrictions', '{}') || jsonb_build_object('wiring_placement_restrictions', wiring_placement_restrictions)
)
where wiring_placement_restrictions is not null
and upper(wiring_placement_restrictions) not in (select upper(name) from restrictions);

alter table builds drop column wiring_placement_restrictions;

insert into build_restrictions
select builds.id, restrictions.id
from builds, restrictions
where upper(component_restrictions) = upper(restrictions.name);

UPDATE builds
SET information = jsonb_set(
    information,
    '{unknown_restrictions}',
    coalesce(information->'unknown_restrictions', '{}') || jsonb_build_object('component_restrictions', component_restrictions)
)
WHERE component_restrictions IS NOT NULL
AND upper(component_restrictions) NOT IN (SELECT upper(name) FROM restrictions);

alter table builds drop column component_restrictions;

CREATE TABLE types (
  id SMALLSERIAL PRIMARY KEY,
  build_category TEXT,
  name TEXT UNIQUE
);

CREATE TABLE build_types (
  build_id BIGINT,
  type_id SMALLINT,
  PRIMARY KEY (build_id, type_id),
  FOREIGN KEY (build_id) REFERENCES builds(id),
  FOREIGN KEY (type_id) REFERENCES types(id)
);

insert into build_types
select builds.id, types.id
from builds, types
where upper(pattern) = upper(types.name);

-- if build pattern is null, that is a Regular door
insert into build_types
select builds.id, types.id
from builds, types
where builds.pattern is null
and types.name = 'Regular';

update builds
set information = information || ('{"unknown_patterns":"' || pattern || '"}')::jsonb
where pattern is not null
and upper(pattern) not in (select upper(name) from types);

alter table builds drop column pattern;

CREATE TABLE versions (
  id SMALLSERIAL PRIMARY KEY,
  edition TEXT,
  major_version TEXT,
  minor_version TEXT,
  patch_number TEXT,
  full_name_temp TEXT
);

CREATE TABLE build_versions (
  build_id BIGINT,
  version_id SMALLINT,
  PRIMARY KEY (build_id, version_id),
  FOREIGN KEY (build_id) REFERENCES builds(id),
  FOREIGN KEY (version_id) REFERENCES versions(id)
);

insert into versions(full_name_temp)
select distinct functional_versions
from builds
where functional_versions is not null
and functional_versions not in (select full_name_temp from versions);

insert into build_versions
select builds.id, versions.id
from builds, versions
where builds.functional_versions = versions.full_name_temp;

alter table builds drop column functional_versions;

CREATE TABLE build_links (
  build_id BIGINT,
  url TEXT,
  media_type TEXT,
  PRIMARY KEY (build_id, url),
  FOREIGN KEY (build_id) REFERENCES builds(id)
);
insert into build_links(build_id, url, media_type)
select id, image_link, 'image'
from builds
where image_link is not null;

insert into build_links(build_id, url, media_type)
select id, video_link, 'video'
from builds
where video_link is not null;

insert into build_links(build_id, url, media_type)
select id, world_download_link, 'world_download'
from builds
where world_download_link is not null;

ALTER TABLE builds DROP COLUMN image_link;
ALTER TABLE builds DROP COLUMN video_link;
ALTER TABLE builds DROP COLUMN world_download_link;
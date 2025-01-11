alter table "public"."messages" add column "content" text;

alter table "public"."builds" drop constraint "original_message_consistency";

update messages
set content = (
  select original_message from builds
  where builds.original_message_id = messages.message_id
);

alter table "public"."builds" drop column "original_message";

alter table "public"."messages" alter column "edited_time" drop default;

alter table "public"."messages" alter column "edited_time" set data type timestamp with time zone using "edited_time"::timestamp with time zone;

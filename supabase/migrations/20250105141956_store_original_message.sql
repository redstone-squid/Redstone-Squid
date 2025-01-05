alter table "public"."builds" add column "ai_generated" boolean not null default false;

alter table "public"."builds" alter column "ai_generated" set not null;

alter table "public"."builds" add column "original_message" text;

alter table "public"."builds" add column "original_message_id" bigint;

alter table "public"."builds" add column "version_spec" text;

alter table "public"."builds" add constraint "original_message_consistency" CHECK ((((original_message_id IS NULL) AND (original_message IS NULL)) OR ((original_message_id IS NOT NULL) AND (original_message IS NOT NULL)))) not valid;

alter table "public"."builds" validate constraint "original_message_consistency";

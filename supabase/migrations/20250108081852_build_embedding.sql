alter table "public"."builds" add column "embedding" vector(1536);

alter table "public"."builds" alter column "ai_generated" drop default;

alter table "public"."messages" drop constraint "messages_vote_session_id_fkey";

alter table "public"."messages" add constraint "messages_vote_session_id_fkey" FOREIGN KEY (vote_session_id) REFERENCES vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE not valid;

alter table "public"."messages" validate constraint "messages_vote_session_id_fkey";

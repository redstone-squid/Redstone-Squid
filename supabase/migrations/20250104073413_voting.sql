alter table "public"."build_vote_sessions" drop constraint "build_vote_sessions_session_id_fkey";

alter table "public"."build_vote_sessions" drop constraint "build_vote_sessions_build_id_fkey";

alter table "public"."build_vote_sessions" drop constraint "build_vote_sessions_pkey";

drop index if exists "public"."build_vote_sessions_pkey";

create table "public"."delete_log_vote_sessions" (
    "vote_session_id" bigint generated by default as identity not null,
    "target_message_id" bigint not null,
    "target_channel_id" bigint not null,
    "target_server_id" bigint not null
);


alter table "public"."delete_log_vote_sessions" enable row level security;

-- alter table "public"."build_vote_sessions" drop column "session_id";
--
-- alter table "public"."build_vote_sessions" add column "vote_session_id" bigint generated by default as identity not null;

alter table "public"."build_vote_sessions" rename column "session_id" to "vote_session_id";

alter table "public"."messages" add column "vote_session_id" bigint;

alter table "public"."messages" alter column "build_id" drop not null;

alter table "public"."vote_sessions" add column "fail_threshold" smallint not null;

alter table "public"."vote_sessions" add column "pass_threshold" smallint not null;

CREATE UNIQUE INDEX delete_log_vote_sessions_pkey ON public.delete_log_vote_sessions USING btree (vote_session_id);

CREATE UNIQUE INDEX build_vote_sessions_pkey ON public.build_vote_sessions USING btree (vote_session_id);

alter table "public"."delete_log_vote_sessions" add constraint "delete_log_vote_sessions_pkey" PRIMARY KEY using index "delete_log_vote_sessions_pkey";

alter table "public"."build_vote_sessions" add constraint "build_vote_sessions_pkey" PRIMARY KEY using index "build_vote_sessions_pkey";

alter table "public"."build_vote_sessions" add constraint "build_vote_sessions_vote_session_id_fkey" FOREIGN KEY (vote_session_id) REFERENCES vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE not valid;

alter table "public"."build_vote_sessions" validate constraint "build_vote_sessions_vote_session_id_fkey";

alter table "public"."delete_log_vote_sessions" add constraint "delete_log_vote_sessions_vote_session_id_fkey" FOREIGN KEY (vote_session_id) REFERENCES vote_sessions(id) ON UPDATE CASCADE ON DELETE CASCADE not valid;

alter table "public"."delete_log_vote_sessions" validate constraint "delete_log_vote_sessions_vote_session_id_fkey";

alter table "public"."messages" add constraint "messages_vote_session_id_fkey" FOREIGN KEY (vote_session_id) REFERENCES vote_sessions(id) ON UPDATE CASCADE ON DELETE RESTRICT not valid;

alter table "public"."messages" validate constraint "messages_vote_session_id_fkey";

alter table "public"."vote_sessions" add constraint "vote_sessions_fail_threshold_check" CHECK ((fail_threshold < 0)) not valid;

alter table "public"."vote_sessions" validate constraint "vote_sessions_fail_threshold_check";

alter table "public"."vote_sessions" add constraint "vote_sessions_pass_threshold_check" CHECK ((pass_threshold > 0)) not valid;

alter table "public"."vote_sessions" validate constraint "vote_sessions_pass_threshold_check";

alter table "public"."build_vote_sessions" add constraint "build_vote_sessions_build_id_fkey" FOREIGN KEY (build_id) REFERENCES builds(id) ON UPDATE CASCADE ON DELETE CASCADE not valid;

alter table "public"."build_vote_sessions" validate constraint "build_vote_sessions_build_id_fkey";

grant delete on table "public"."delete_log_vote_sessions" to "anon";

grant insert on table "public"."delete_log_vote_sessions" to "anon";

grant references on table "public"."delete_log_vote_sessions" to "anon";

grant select on table "public"."delete_log_vote_sessions" to "anon";

grant trigger on table "public"."delete_log_vote_sessions" to "anon";

grant truncate on table "public"."delete_log_vote_sessions" to "anon";

grant update on table "public"."delete_log_vote_sessions" to "anon";

grant delete on table "public"."delete_log_vote_sessions" to "authenticated";

grant insert on table "public"."delete_log_vote_sessions" to "authenticated";

grant references on table "public"."delete_log_vote_sessions" to "authenticated";

grant select on table "public"."delete_log_vote_sessions" to "authenticated";

grant trigger on table "public"."delete_log_vote_sessions" to "authenticated";

grant truncate on table "public"."delete_log_vote_sessions" to "authenticated";

grant update on table "public"."delete_log_vote_sessions" to "authenticated";

grant delete on table "public"."delete_log_vote_sessions" to "service_role";

grant insert on table "public"."delete_log_vote_sessions" to "service_role";

grant references on table "public"."delete_log_vote_sessions" to "service_role";

grant select on table "public"."delete_log_vote_sessions" to "service_role";

grant trigger on table "public"."delete_log_vote_sessions" to "service_role";

grant truncate on table "public"."delete_log_vote_sessions" to "service_role";

grant update on table "public"."delete_log_vote_sessions" to "service_role";



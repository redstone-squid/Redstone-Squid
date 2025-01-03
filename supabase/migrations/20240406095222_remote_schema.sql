
SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

CREATE EXTENSION IF NOT EXISTS "pgsodium" WITH SCHEMA "pgsodium";

COMMENT ON SCHEMA "public" IS 'standard public schema';

CREATE EXTENSION IF NOT EXISTS "pg_graphql" WITH SCHEMA "graphql";

CREATE EXTENSION IF NOT EXISTS "pg_stat_statements" WITH SCHEMA "extensions";

CREATE EXTENSION IF NOT EXISTS "pgcrypto" WITH SCHEMA "extensions";

CREATE EXTENSION IF NOT EXISTS "pgjwt" WITH SCHEMA "extensions";

CREATE EXTENSION IF NOT EXISTS "supabase_vault" WITH SCHEMA "vault";

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA "extensions";

SET default_tablespace = '';

SET default_table_access_method = "heap";

CREATE TABLE IF NOT EXISTS "public"."messages" (
    "server_id" bigint NOT NULL,
    "submission_id" bigint NOT NULL,
    "channel_id" bigint,
    "message_id" bigint NOT NULL,
    "last_updated" timestamp without time zone DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE "public"."messages" OWNER TO "postgres";

CREATE OR REPLACE FUNCTION "public"."get_outdated_messages"("server_id_input" bigint) RETURNS SETOF "public"."messages"
    LANGUAGE "plpgsql"
    AS $$
  begin
    return query select messages.*
    from messages join submissions
    on (messages.submission_id = submissions.submission_id)
    where messages.last_updated < submissions.last_update
    and messages.server_id = server_id_input
    and submissions.submission_status = 1;  -- accepted
  end;
$$;

ALTER FUNCTION "public"."get_outdated_messages"("server_id_input" bigint) OWNER TO "postgres";

CREATE TABLE IF NOT EXISTS "public"."submissions" (
    "submission_id" bigint NOT NULL,
    "submission_status" smallint NOT NULL,
    "last_update" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "record_category" "text",
    "door_width" integer NOT NULL,
    "door_height" integer NOT NULL,
    "pattern" "text",
    "door_type" "text" NOT NULL,
    "wiring_placement_restrictions" "text",
    "component_restrictions" "text",
    "information" "text",
    "build_width" integer NOT NULL,
    "build_height" integer NOT NULL,
    "build_depth" integer NOT NULL,
    "normal_closing_time" bigint,
    "normal_opening_time" bigint,
    "visible_closing_time" bigint,
    "visible_opening_time" bigint,
    "date_of_creation" "text",
    "submission_time" timestamp without time zone DEFAULT CURRENT_TIMESTAMP,
    "creators_ign" "text" DEFAULT ''::"text",
    "locationality" "text",
    "directionality" "text",
    "functional_versions" "text",
    "image_link" "text",
    "video_link" "text",
    "world_download_link" "text",
    "server_ip" "text",
    "coordinates" "text",
    "command_to_build" "text",
    "submitted_by" "text",
    CONSTRAINT "check_directionality" CHECK (("directionality" = ANY (ARRAY[NULL::"text", 'Directional'::"text", 'Directional with fixes'::"text"]))),
    CONSTRAINT "check_door_type" CHECK (("door_type" = ANY (ARRAY['Door'::"text", 'Trapdoor'::"text", 'Skydoor'::"text"]))),
    CONSTRAINT "check_locationality" CHECK (("locationality" = ANY (ARRAY[NULL::"text", 'Locational'::"text", 'Locational with fixes'::"text"]))),
    CONSTRAINT "check_record_category" CHECK (("record_category" = ANY (ARRAY['Smallest'::"text", 'Fastest'::"text", 'First'::"text", 'Smallest Fastest'::"text", 'Fastest Smallest'::"text", NULL::"text"]))),
    CONSTRAINT "check_status" CHECK (("submission_status" = ANY (ARRAY[0, 1, 2]))),
    CONSTRAINT "submissions_absolute_closing_time_check" CHECK ((("visible_closing_time" >= 0) OR ("visible_closing_time" IS NULL))),
    CONSTRAINT "submissions_absolute_opening_time_check" CHECK ((("visible_opening_time" >= 0) OR ("visible_opening_time" IS NULL))),
    CONSTRAINT "submissions_build_depth_check" CHECK (("build_depth" > 0)),
    CONSTRAINT "submissions_build_height_check" CHECK (("build_height" > 0)),
    CONSTRAINT "submissions_build_width_check" CHECK (("build_width" > 0)),
    CONSTRAINT "submissions_relative_closing_time_check" CHECK (("normal_closing_time" >= 0)),
    CONSTRAINT "submissions_relative_opening_time_check" CHECK (("normal_opening_time" >= 0))
);

ALTER TABLE "public"."submissions" OWNER TO "postgres";

CREATE OR REPLACE FUNCTION "public"."get_unsent_submissions"("server_id_input" bigint) RETURNS SETOF "public"."submissions"
    LANGUAGE "plpgsql"
    AS $$
  begin
    return query select *
    from submissions
    where submission_id not in (
      select submission_id
      from messages
      where server_id = server_id_input
      )
    and submission_status = 1;  -- accepted
  end;
$$;

ALTER FUNCTION "public"."get_unsent_submissions"("server_id_input" bigint) OWNER TO "postgres";

CREATE TABLE IF NOT EXISTS "public"."server_settings" (
    "server_id" bigint NOT NULL,
    "smallest_channel_id" bigint,
    "fastest_channel_id" bigint,
    "first_channel_id" bigint,
    "builds_channel_id" bigint,
    "voting_channel_id" bigint
);

ALTER TABLE "public"."server_settings" OWNER TO "postgres";

CREATE SEQUENCE IF NOT EXISTS "public"."submissions_submission_id_seq"
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;

ALTER TABLE "public"."submissions_submission_id_seq" OWNER TO "postgres";

ALTER SEQUENCE "public"."submissions_submission_id_seq" OWNED BY "public"."submissions"."submission_id";

ALTER TABLE ONLY "public"."submissions" ALTER COLUMN "submission_id" SET DEFAULT "nextval"('"public"."submissions_submission_id_seq"'::"regclass");

ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "messages_pkey" PRIMARY KEY ("message_id");

ALTER TABLE ONLY "public"."server_settings"
    ADD CONSTRAINT "server_settings_fastest_channel_id_key" UNIQUE ("fastest_channel_id");

ALTER TABLE ONLY "public"."server_settings"
    ADD CONSTRAINT "server_settings_first_channel_id_key" UNIQUE ("first_channel_id");

ALTER TABLE ONLY "public"."server_settings"
    ADD CONSTRAINT "server_settings_pkey" PRIMARY KEY ("server_id");

ALTER TABLE ONLY "public"."server_settings"
    ADD CONSTRAINT "server_settings_smallest_channel_id_key" UNIQUE ("smallest_channel_id");

ALTER TABLE ONLY "public"."submissions"
    ADD CONSTRAINT "submissions_pkey" PRIMARY KEY ("submission_id");

ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "public_messages_server_id_fkey" FOREIGN KEY ("server_id") REFERENCES "public"."server_settings"("server_id");

ALTER TABLE ONLY "public"."messages"
    ADD CONSTRAINT "public_messages_submission_id_fkey" FOREIGN KEY ("submission_id") REFERENCES "public"."submissions"("submission_id");

ALTER PUBLICATION "supabase_realtime" OWNER TO "postgres";

GRANT USAGE ON SCHEMA "public" TO "postgres";
GRANT USAGE ON SCHEMA "public" TO "anon";
GRANT USAGE ON SCHEMA "public" TO "authenticated";
GRANT USAGE ON SCHEMA "public" TO "service_role";

GRANT ALL ON TABLE "public"."messages" TO "anon";
GRANT ALL ON TABLE "public"."messages" TO "authenticated";
GRANT ALL ON TABLE "public"."messages" TO "service_role";

GRANT ALL ON FUNCTION "public"."get_outdated_messages"("server_id_input" bigint) TO "anon";
GRANT ALL ON FUNCTION "public"."get_outdated_messages"("server_id_input" bigint) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_outdated_messages"("server_id_input" bigint) TO "service_role";

GRANT ALL ON TABLE "public"."submissions" TO "anon";
GRANT ALL ON TABLE "public"."submissions" TO "authenticated";
GRANT ALL ON TABLE "public"."submissions" TO "service_role";

GRANT ALL ON FUNCTION "public"."get_unsent_submissions"("server_id_input" bigint) TO "anon";
GRANT ALL ON FUNCTION "public"."get_unsent_submissions"("server_id_input" bigint) TO "authenticated";
GRANT ALL ON FUNCTION "public"."get_unsent_submissions"("server_id_input" bigint) TO "service_role";

GRANT ALL ON TABLE "public"."server_settings" TO "anon";
GRANT ALL ON TABLE "public"."server_settings" TO "authenticated";
GRANT ALL ON TABLE "public"."server_settings" TO "service_role";

GRANT ALL ON SEQUENCE "public"."submissions_submission_id_seq" TO "anon";
GRANT ALL ON SEQUENCE "public"."submissions_submission_id_seq" TO "authenticated";
GRANT ALL ON SEQUENCE "public"."submissions_submission_id_seq" TO "service_role";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON SEQUENCES  TO "service_role";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON FUNCTIONS  TO "service_role";

ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "postgres";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "anon";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "authenticated";
ALTER DEFAULT PRIVILEGES FOR ROLE "postgres" IN SCHEMA "public" GRANT ALL ON TABLES  TO "service_role";

RESET ALL;

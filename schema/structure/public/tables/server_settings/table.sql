CREATE TABLE
  public.server_settings (
    server_id bigint NOT NULL DEFAULT NULL,
    smallest_channel_id bigint NULL DEFAULT NULL,
    fastest_channel_id bigint NULL DEFAULT NULL,
    first_channel_id bigint NULL DEFAULT NULL,
    builds_channel_id bigint NULL DEFAULT NULL,
    voting_channel_id bigint NULL DEFAULT NULL,
    in_server boolean NOT NULL DEFAULT true,
    trusted_roles_ids int8[] NULL DEFAULT NULL,
    staff_roles_ids int8[] NULL DEFAULT NULL
  );

;

CREATE UNIQUE INDEX server_settings_fastest_channel_id_key ON public.server_settings USING btree (fastest_channel_id);

CREATE UNIQUE INDEX server_settings_first_channel_id_key ON public.server_settings USING btree (first_channel_id);

CREATE UNIQUE INDEX server_settings_pkey ON public.server_settings USING btree (server_id);

CREATE UNIQUE INDEX server_settings_smallest_channel_id_key ON public.server_settings USING btree (smallest_channel_id);
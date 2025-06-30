CREATE
OR REPLACE FUNCTION public.get_outdated_messages (server_id_input bigint) RETURNS SETOF messages LANGUAGE plpgsql AS $function$begin
    return query select messages.*
    from messages join builds
    on (messages.submission_id = builds.submission_id)
    where messages.last_updated < builds.last_update
    and messages.server_id = server_id_input
    and builds.submission_status = 1;  -- accepted
  end;$function$;
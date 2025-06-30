CREATE
OR REPLACE FUNCTION public.get_unsent_builds (server_id_input bigint) RETURNS SETOF builds LANGUAGE plpgsql AS $function$
  begin
    return query select *
    from builds
    where id not in (
      select build_id
      from messages
      where server_id = server_id_input
      )
    and submission_status = 1;  -- accepted
  end;
$function$;
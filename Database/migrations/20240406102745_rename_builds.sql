alter table submissions
    rename column submission_id to id;

alter table submissions
rename to builds;

alter table messages
rename column submission_id to build_id;

drop function get_unsent_submissions(bigint);
create or replace function get_unsent_builds (server_id_input bigint)
returns setof builds
as $$
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
$$ language plpgsql;

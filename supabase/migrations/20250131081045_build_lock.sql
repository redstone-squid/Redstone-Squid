alter table builds add column is_locked boolean not null default false;
alter table builds add column locked_at timestamptz default null;

-- Add trigger to set locked_at when is_locked is set
create or replace function set_locked_at() returns trigger as $$
begin
  if new.is_locked then
    new.locked_at := now();
  else
    new.locked_at := null;
  end if;
  return new;
end;
$$ language plpgsql;

create trigger set_locked_at before update on builds for each row execute function set_locked_at();
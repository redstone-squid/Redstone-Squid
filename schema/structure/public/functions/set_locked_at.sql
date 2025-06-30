CREATE
OR REPLACE FUNCTION public.set_locked_at () RETURNS trigger LANGUAGE plpgsql AS $function$
begin
  if new.is_locked then
    new.locked_at := now();
  else
    new.locked_at := null;
  end if;
  return new;
end;
$function$;
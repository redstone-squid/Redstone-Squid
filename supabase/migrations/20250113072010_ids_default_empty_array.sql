alter table server_settings alter column staff_roles_ids set default '{}';
update server_settings set staff_roles_ids = '{}' where staff_roles_ids is null;
alter table server_settings alter column staff_roles_ids drop not null;
alter table server_settings alter column trusted_roles_ids set default '{}';
update server_settings set trusted_roles_ids = '{}' where trusted_roles_ids is null;
alter table server_settings alter column trusted_roles_ids drop not null;

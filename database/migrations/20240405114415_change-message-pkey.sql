alter table messages
drop constraint messages_pkey;

alter table messages
add constraint messages_pkey primary key (message_id);
 create table vote_session_messages (
    "vote_session_id" bigint not null,
    "message_id" bigint not null,
    primary key ("vote_session_id", "message_id"),
    foreign key ("vote_session_id") references vote_sessions (id) on delete cascade,
    foreign key ("message_id") references messages (message_id) on delete cascade
);

insert into vote_session_messages (vote_session_id, message_id)
select vote_session_id, message_id from messages
where vote_session_id is not null;

alter table messages drop column vote_session_id;

update messages set author_id = 0 where author_id is null;

alter table messages alter column author_id set not null;

alter table builds alter column edited_time set data type timestamp with time zone using edited_time::timestamp with time zone;

alter table messages rename column message_id to id;

alter table messages add column vote_session_id bigint;
alter table messages
    add constraint messages_vote_session_id_fkey
    foreign key (vote_session_id) references vote_sessions (id) on delete set null;
update messages
    set vote_session_id = vsm.vote_session_id
from vote_session_messages vsm
    where messages.id = vsm.message_id;

drop table if exists vote_session_messages;
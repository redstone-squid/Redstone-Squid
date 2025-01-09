alter table "public"."builds" add constraint "builds_original_message_id_fkey" FOREIGN KEY (original_message_id) REFERENCES messages(message_id) ON UPDATE CASCADE ON DELETE SET NULL not valid;

alter table "public"."builds" validate constraint "builds_original_message_id_fkey";
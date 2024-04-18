ALTER TABLE doors
drop CONSTRAINT doors_build_id_fkey;

ALTER TABLE doors
ADD CONSTRAINT doors_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE extenders
drop CONSTRAINT extenders_build_id_fkey;

ALTER TABLE extenders
ADD CONSTRAINT extenders_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE entrances
drop CONSTRAINT entrances_build_id_fkey;

ALTER TABLE entrances
ADD CONSTRAINT entrances_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE utilities
drop CONSTRAINT utilities_build_id_fkey;

ALTER TABLE utilities
ADD CONSTRAINT utilities_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE build_creators
drop CONSTRAINT build_creators_build_id_fkey;

ALTER TABLE build_creators
ADD CONSTRAINT build_creators_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE build_restrictions
drop CONSTRAINT build_restrictions_build_id_fkey;

ALTER TABLE build_restrictions
ADD CONSTRAINT build_restrictions_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE build_restrictions
drop CONSTRAINT build_restrictions_restriction_id_fkey;

ALTER TABLE build_restrictions
ADD CONSTRAINT build_restrictions_restriction_id_fkey
    FOREIGN KEY (restriction_id)
    REFERENCES restrictions
        (id)
    ON DELETE RESTRICT ON UPDATE NO ACTION;

ALTER TABLE build_types
drop CONSTRAINT build_types_build_id_fkey;

ALTER TABLE build_types
ADD CONSTRAINT build_types_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE build_types
drop CONSTRAINT build_types_type_id_fkey;

ALTER TABLE build_types
ADD CONSTRAINT build_types_type_id_fkey
    FOREIGN KEY (type_id)
    REFERENCES types
        (id)
    ON DELETE RESTRICT ON UPDATE NO ACTION;

ALTER TABLE build_versions
drop CONSTRAINT build_versions_build_id_fkey;

ALTER TABLE build_versions
ADD CONSTRAINT build_versions_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE build_versions
drop CONSTRAINT build_versions_version_id_fkey;

ALTER TABLE build_versions
ADD CONSTRAINT build_versions_version_id_fkey
    FOREIGN KEY (version_id)
    REFERENCES versions
        (id)
    ON DELETE RESTRICT ON UPDATE NO ACTION;

ALTER TABLE build_links
drop CONSTRAINT build_links_build_id_fkey;

ALTER TABLE build_links
ADD CONSTRAINT build_links_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;

ALTER TABLE messages
drop CONSTRAINT public_messages_server_id_fkey;

ALTER TABLE messages
ADD CONSTRAINT public_messages_server_id_fkey
    FOREIGN KEY (server_id)
    REFERENCES server_settings
        (server_id)
    ON DELETE RESTRICT ON UPDATE NO ACTION;

ALTER TABLE messages
drop CONSTRAINT public_messages_submission_id_fkey;

ALTER TABLE messages
ADD CONSTRAINT public_messages_build_id_fkey
    FOREIGN KEY (build_id)
    REFERENCES builds
        (id)
    ON DELETE CASCADE ON UPDATE NO ACTION;
CREATE OR REPLACE FUNCTION delete_orphaned_build_vote_sessions_after_builds_delete()
RETURNS TRIGGER AS $$
BEGIN
    DELETE FROM vote_sessions
    WHERE id IN (
        SELECT vote_sessions.id
        FROM vote_sessions vs
        LEFT JOIN build_vote_sessions bvs ON vs.id = bvs.vote_session_id
        LEFT JOIN delete_log_vote_sessions dvs ON vs.id = dvs.vote_session_id
        WHERE bvs.vote_session_id IS NULL AND dvs.vote_session_id IS NULL
    );
    RETURN NULL; -- Statement-level triggers do not use OLD or NEW
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds
AFTER DELETE ON builds
FOR EACH STATEMENT
EXECUTE FUNCTION delete_orphaned_build_vote_sessions_after_builds_delete();
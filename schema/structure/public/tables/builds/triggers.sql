CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds
AFTER DELETE ON public.builds FOR EACH STATEMENT
EXECUTE FUNCTION delete_orphaned_build_vote_sessions_after_builds_delete ();

CREATE TRIGGER set_locked_at BEFORE
UPDATE ON public.builds FOR EACH ROW
EXECUTE FUNCTION set_locked_at ();

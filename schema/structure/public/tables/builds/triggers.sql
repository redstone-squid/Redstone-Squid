CREATE TRIGGER builds_refresh_smallest_door
AFTER INSERT
OR DELETE
OR
UPDATE ON public.builds FOR EACH ROW
EXECUTE FUNCTION trg_refresh_smallest_door_from_builds ();

CREATE TRIGGER delete_orphaned_build_vote_sessions_after_builds
AFTER DELETE ON public.builds FOR EACH STATEMENT
EXECUTE FUNCTION delete_orphaned_build_vote_sessions_after_builds_delete ();

CREATE TRIGGER set_locked_at BEFORE
UPDATE ON public.builds FOR EACH ROW
EXECUTE FUNCTION set_locked_at ();
-- Lease and embedding writes change nothing a reader can see, so they must not queue work.
-- Listing the ignored columns makes any new column count as content.
CREATE TRIGGER builds_enqueue_build_change_on_update
    AFTER UPDATE ON public.builds
    FOR EACH ROW
    WHEN (
        (to_jsonb(OLD.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])
        IS DISTINCT FROM (to_jsonb(NEW.*) - ARRAY['embedding', 'locked_at', 'lock_token', 'lock_expires_at'])
    )
    EXECUTE FUNCTION public.enqueue_build_change();

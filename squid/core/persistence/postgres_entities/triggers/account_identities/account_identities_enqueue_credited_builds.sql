CREATE TRIGGER account_identities_enqueue_credited_builds
    AFTER INSERT OR DELETE OR UPDATE OF account_id, provider, subject, is_public ON public.account_identities
    FOR EACH ROW
    EXECUTE FUNCTION public.enqueue_identity_credited_builds();

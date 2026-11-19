CREATE CONSTRAINT TRIGGER extenders_category_subtype_check
    AFTER INSERT OR DELETE OR UPDATE OF build_id ON public.extenders
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    EXECUTE FUNCTION public.enforce_build_category_subtype();

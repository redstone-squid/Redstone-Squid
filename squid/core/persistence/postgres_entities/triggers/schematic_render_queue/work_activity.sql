CREATE TRIGGER schematic_render_queue_work_activity
    AFTER INSERT OR UPDATE OR DELETE ON public.schematic_render_queue
    FOR EACH ROW EXECUTE FUNCTION public.record_work_activity('queue', 'build_id');

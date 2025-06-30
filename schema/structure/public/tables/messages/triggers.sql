CREATE TRIGGER update_messages_updated_at BEFORE
UPDATE ON public.messages FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column ();
CREATE POLICY Enable insert for authenticated users only ON public.vote_sessions FOR INSERT TO authenticated,
service_role USING (true)
WITH
  CHECK (true);

;